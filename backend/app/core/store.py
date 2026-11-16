"""Everything auth keeps in Redis: OTP lifecycle, session cache, rate limits.

Each multi-step write is one Lua script. The same sequence from Python would let
two concurrent requests each burn an OTP attempt, or spend a verified OTP twice
before the delete lands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    pass

from app.core.config import get_settings
from app.core.redis_conn import get_redis

OtpPurpose = Literal['signup', 'login', 'reset', 'change_password', 'email_change', 'restore']
RevocationReason = Literal[
    'logout',
    'password_change',
    'email_change',
    'device_switch',
    'token_reuse',
    'account_deleted',
    'admin',
]


class _Script:
    """One Lua program, called as `await script([keys], [args])`.

    Nothing is cached across calls on purpose. `register_script` only builds a
    `Script` object and sha1s the source locally - the network work is the EVALSHA
    that happens when it is called - so a cache would save microseconds while pinning
    the script to one Redis client. Holding that reference is how a reconnect (or a
    test that rebuilds the client per event loop) ends up issuing commands on a dead
    connection.
    """

    source: ClassVar[str] = ''

    async def __call__(self, keys: list[str], args: list[Any] | None = None) -> Any:
        script = get_redis().register_script(self.source)
        return await script(keys=keys, args=args or [])


class _OtpCreateScript(_Script):
    """Keys: [otp, hourly, cooldown]. Negative results are retry-after hints."""

    source: ClassVar[str] = """
local ttl = tonumber(ARGV[1])
local hour = tonumber(ARGV[4])
local cd = redis.call('TTL', KEYS[3])
if tonumber(ARGV[5]) > 0 and cd > 0 then
  return -cd
end
if tonumber(ARGV[3]) > 0 and tonumber(redis.call('GET', KEYS[2]) or '0') >= tonumber(ARGV[3]) then
  return -1
end
redis.call('HSET', KEYS[1], 'h', ARGV[2], 'a', '0')
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], hour)
redis.call('SET', KEYS[3], '1', 'EX', ttl)
return 1
"""


class _OtpResendScript(_Script):
    """Same contract as create, but re-issuing into an existing session id."""

    source: ClassVar[str] = """
local ttl = tonumber(ARGV[1])
local hour = tonumber(ARGV[4])
local cd = redis.call('TTL', KEYS[3])
if tonumber(ARGV[5]) > 0 and cd > 0 then
  return -cd
end
if tonumber(ARGV[3]) > 0 and tonumber(redis.call('GET', KEYS[2]) or '0') >= tonumber(ARGV[3]) then
  return -1
end
if redis.call('EXISTS', KEYS[1]) == 0 then
  return -2
end
redis.call('HSET', KEYS[1], 'h', ARGV[2], 'a', '0')
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], hour)
redis.call('SET', KEYS[3], '1', 'EX', ttl)
return 1
"""


class _OtpVerifyScript(_Script):
    """Return code: 1 ok, 0 wrong code, -2 locked, -1 no such challenge.

    Every name is declared local on purpose. A real Redis raises
    "Script attempted to access nonexistent global variable" for a typo, while the
    Lua in fakeredis hands back nil and the script quietly keeps working - so a
    bare name here is a bug that only shows up in production. Run the suite against
    a server too (REDIS_FAKE=0), not only the fake.
    """

    source: ClassVar[str] = """
local expected = redis.call('HGET', KEYS[1], 'h')
if not expected then
  return {-1, 0}
end
if expected ~= ARGV[1] then
  local attempts = redis.call('HINCRBY', KEYS[1], 'a', '1')
  if attempts >= tonumber(ARGV[2]) then
    redis.call('DEL', KEYS[1])
    return {-2, attempts}
  end
  return {0, attempts}
end
redis.call('DEL', KEYS[1])
return {1, 0}
"""


class _OtpSpendScript(_Script):
    """Delete the OTP and its cooldown; the hourly counter deliberately survives."""

    source: ClassVar[str] = """
local gone = redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
return gone
"""


class _RateLimitScript(_Script):
    source: ClassVar[str] = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  ttl = tonumber(ARGV[1])
end
if n > tonumber(ARGV[2]) then
  return {0, ttl}
end
return {1, ttl}
"""


class _SessionCacheScript(_Script):
    source: ClassVar[str] = """
local raw = redis.call('HGET', KEYS[1], 'u')
if not raw then
  return {0, ''}
end
local d = cjson.decode(raw)
if tostring(d['p']) ~= ARGV[1] or tostring(d['t']) ~= ARGV[2] then
  return {0, ''}
end
return {1, raw}
"""


_otp_create = _OtpCreateScript()
_otp_resend = _OtpResendScript()
_otp_verify = _OtpVerifyScript()
_otp_spend = _OtpSpendScript()
_rate_limit = _RateLimitScript()
_session_cache = _SessionCacheScript()


def _sends(s) -> int:
    """0 disables the hourly cap: RATE_LIMIT_ENABLED=false must not break tests
    that legitimately send several codes to one address.
    """
    return s.otp_max_sends_per_hour if s.rate_limit_enabled else 0


def _cooldown(s) -> int:
    return s.otp_resend_cooldown_seconds if s.rate_limit_enabled else 0


@dataclass(frozen=True, slots=True)
class OtpTicket:
    session_id: str
    expires_in: int


class OtpError(Exception):
    """kind is a machine-readable reason, never a secret."""

    def __init__(self, kind: str, retry_after: int = 0) -> None:
        self.kind = kind
        self.retry_after = retry_after
        super().__init__(kind)


class OtpVerifyError(Exception):
    def __init__(self, kind: str, attempts_left: int | None = None) -> None:
        self.kind = kind
        self.attempts_left = attempts_left
        super().__init__(kind)


def otp_key(purpose: str, session_id: str) -> str:
    return f'otp:{purpose}:{session_id}'


def otp_hourly_key(purpose: str, identity: str) -> str:
    return f'otp_rl:{purpose}:{identity}'


def otp_cooldown_key(purpose: str, identity: str) -> str:
    return f'otp_cd:{purpose}:{identity}'


def session_key(session_id: str) -> str:
    return f'sess:{session_id}'


def profile_cache_key(session_id: str) -> str:
    """Session-scoped profile blob. Cleared with the auth session on logout/revoke."""
    return f'sess:{session_id}:p'


def _session_cache_ttl() -> int:
    """Short TTL by design: bounds the window if a revoke fails to drop the key.

    Do not stretch this to the access-token lifetime — that would keep a stolen
    identity cache alive long after logout.
    """
    return max(1, int(get_settings().session_cache_seconds))


def _otp_keys(purpose: str, session_id: str, identity: str) -> list[str]:
    return [
        otp_key(purpose, session_id),
        otp_hourly_key(purpose, identity),
        otp_cooldown_key(purpose, identity),
    ]


async def create_otp(*, purpose: OtpPurpose, session_id: str, otp_hash: str, identity: str) -> OtpTicket:
    """Issue the first OTP for a session. Also used to restart a failed flow."""
    s = get_settings()
    code = await _otp_create(
        _otp_keys(purpose, session_id, identity),
        [s.otp_ttl_seconds, otp_hash, _sends(s), 3600, _cooldown(s)],
    )
    if code < 0:
        raise OtpError('too_many_sends' if code == -1 else 'cooldown', retry_after=abs(int(code)))
    return OtpTicket(session_id=session_id, expires_in=s.otp_ttl_seconds)


async def resend_otp(*, purpose: OtpPurpose, session_id: str, otp_hash: str, identity: str) -> OtpTicket:
    s = get_settings()
    code = await _otp_resend(
        _otp_keys(purpose, session_id, identity),
        [s.otp_ttl_seconds, otp_hash, _sends(s), 3600, _cooldown(s)],
    )
    if code < 0:
        kind = {
            -1: 'too_many_sends',
            -2: 'expired_or_used',
        }.get(int(code), 'cooldown')
        raise OtpError(kind, retry_after=abs(int(code)))
    return OtpTicket(session_id=session_id, expires_in=s.otp_ttl_seconds)


async def verify_otp(*, purpose: OtpPurpose, session_id: str, otp_hash: str) -> None:
    """Constant-time-ish compare against the stored hash. Raises OtpVerifyError."""
    s = get_settings()
    code, attempts = await _otp_verify([otp_key(purpose, session_id)], [otp_hash, s.otp_max_attempts])
    code = int(code)
    if code == 1:
        return
    if code == -1:
        raise OtpVerifyError('expired_or_used')
    if code == -2:
        raise OtpVerifyError('too_many_attempts')
    raise OtpVerifyError('invalid', attempts_left=max(s.otp_max_attempts - int(attempts), 0))


async def get_otp_state(*, purpose: OtpPurpose, session_id: str) -> dict[str, str]:
    """Read the side-channel fields (user id, destination) of a live challenge."""
    raw = await get_redis().hgetall(otp_key(purpose, session_id))
    return {str(k): str(v) for k, v in raw.items()}


async def drop_otp(*, purpose: OtpPurpose, session_id: str, identity: str) -> None:
    """Clear a consumed OTP and its cooldown so the next flow is not throttled."""
    await _otp_spend([otp_key(purpose, session_id), otp_cooldown_key(purpose, identity)])


async def write_session_cache(
    *,
    session_id: str,
    user_id: str,
    role: str,
    is_active: bool,
    is_email_verified: bool,
    pwd_epoch: str,
    permissions: tuple[str, ...] = (),
    token_version: int = 0,
) -> None:
    """Auth identity only (no email/phone/address). Gated on read by pwd+ver."""
    client = get_redis()
    payload = json.dumps(
        {
            'u': user_id,
            'r': role,
            'a': '1' if is_active else '0',
            'v': '1' if is_email_verified else '0',
            'p': pwd_epoch,
            'x': list(permissions),
            't': str(token_version),
        }
    )
    key = session_key(session_id)
    pipe = client.pipeline(transaction=True)
    pipe.hset(key, 'u', payload)
    pipe.expire(key, _session_cache_ttl())
    await pipe.execute()


async def read_session_cache(
    *, session_id: str, pwd_epoch: str, token_version: int = 0
) -> dict[str, Any] | None:
    """Returns the cached identity only when the password epoch still matches."""
    ok, payload = await _session_cache([session_key(session_id)], [pwd_epoch, str(token_version)])
    if not int(ok) or not payload:
        return None
    result: dict[str, Any] = json.loads(payload)
    return result


_PROFILE_FORBIDDEN = frozenset(
    {
        'password',
        'password_hash',
        'access_token',
        'refresh_token',
        'otp',
        'otp_hmac_secret',
        'token',
    }
)


async def write_profile_cache(*, session_id: str, profile: dict[str, Any]) -> None:
    """Cache the SPA ``/me`` payload for this session. Never store secrets."""
    clean = {k: v for k, v in profile.items() if k not in _PROFILE_FORBIDDEN}
    if 'user_id' not in clean:
        raise ValueError('profile cache requires user_id')
    await get_redis().set(profile_cache_key(session_id), json.dumps(clean), ex=_session_cache_ttl())


async def read_profile_cache(*, session_id: str, user_id: str) -> dict[str, Any] | None:
    """Hit only when the blob belongs to this user (guards key mix-ups)."""
    raw = await get_redis().get(profile_cache_key(session_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await get_redis().delete(profile_cache_key(session_id))
        return None
    if not isinstance(data, dict):
        await get_redis().delete(profile_cache_key(session_id))
        return None
    # Ownership mismatch: miss only (do not delete — avoids wiping a good blob on a bad caller).
    if str(data.get('user_id') or '') != str(user_id):
        return None
    return data


async def drop_session_cache(*, session_id: str) -> None:
    """Drop auth identity + profile blob for this device session."""
    await get_redis().delete(session_key(session_id), profile_cache_key(session_id))


async def warm_session_caches(
    *,
    session_id: str,
    user_id: str,
    role: str,
    is_active: bool,
    is_email_verified: bool,
    pwd_epoch: str,
    permissions: tuple[str, ...],
    token_version: int,
    profile: dict[str, Any],
) -> None:
    """Login / refresh / profile-update: rewrite both keys with a fresh TTL."""
    await write_session_cache(
        session_id=session_id,
        user_id=user_id,
        role=role,
        is_active=is_active,
        is_email_verified=is_email_verified,
        pwd_epoch=pwd_epoch,
        permissions=permissions,
        token_version=token_version,
    )
    await write_profile_cache(session_id=session_id, profile=profile)


async def rate_limit(*, name: str, identity: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Fixed-window limiter. Returns (allowed, retry_after_seconds)."""
    if not get_settings().rate_limit_enabled:
        return True, 0
    code = await _rate_limit([f'rl:{name}:{identity}'], [window_seconds, limit])
    return int(code[0]) == 1, int(code[1])


__all__ = [
    'OtpError',
    'OtpPurpose',
    'OtpTicket',
    'OtpVerifyError',
    'RevocationReason',
    'create_otp',
    'drop_otp',
    'drop_session_cache',
    'get_otp_state',
    'profile_cache_key',
    'rate_limit',
    'read_profile_cache',
    'read_session_cache',
    'resend_otp',
    'session_key',
    'verify_otp',
    'warm_session_caches',
    'write_profile_cache',
    'write_session_cache',
]
