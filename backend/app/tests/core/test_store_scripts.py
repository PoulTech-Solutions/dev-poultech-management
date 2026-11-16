"""The Lua scripts themselves, tested against whichever Redis the run is using.

The HTTP flow tests cover the service layer; these cover the scripts, which is where
the real rules live: one-time use, attempt counting, delete-on-success. They are worth
a file of their own because a script bug can hide in one Redis and not the other -
fakeredis runs a lenient Lua where an undeclared name is `nil`, while a real server
refuses any global the script did not declare. So:

    REDIS_FAKE=1 python -m pytest    # hermetic, what CI gets by default
    REDIS_FAKE=0 python -m pytest    # against the server in docker-compose.yml

Both must pass. These are the tests that tell them apart.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.core.redis_conn import get_redis
from app.core.security import hash_otp
from app.core.store import (
    OtpError,
    OtpPurpose,
    OtpVerifyError,
    create_otp,
    drop_otp,
    drop_session_cache,
    get_otp_state,
    otp_cooldown_key,
    otp_key,
    read_session_cache,
    resend_otp,
    verify_otp,
)


def _sid() -> str:
    return uuid.uuid4().hex


def _hash(code: str, *, purpose: OtpPurpose, sid: str) -> str:
    return hash_otp(code, session_id=sid, purpose=purpose)


async def _issue(purpose: OtpPurpose, sid: str, code: str, *, identity: str = 'someone@example.com') -> None:
    await create_otp(
        purpose=purpose, session_id=sid, otp_hash=_hash(code, purpose=purpose, sid=sid), identity=identity
    )


async def _accept(purpose: OtpPurpose, sid: str, code: str) -> None:
    """A no-raise call: verify_otp returns None on success and raises otherwise."""
    await verify_otp(purpose=purpose, session_id=sid, otp_hash=_hash(code, purpose=purpose, sid=sid))


@pytest.fixture
async def clean_otp():
    """Delete every challenge this test created, whatever the assertions did."""
    used: list[tuple[str, str]] = []
    yield used
    if used:
        await get_redis().delete(*[otp_key(purpose, sid) for purpose, sid in used])


async def test_a_correct_code_is_worth_exactly_one_use(clean_otp) -> None:
    purpose: OtpPurpose = 'login'
    sid, code = _sid(), '481920'
    clean_otp.append((purpose, sid))
    await _issue(purpose, sid, code)

    # This line is the whole point of the file: the success path is what a lenient
    # Lua forgives and a real server does not.
    await _accept(purpose, sid, code)
    assert await get_otp_state(purpose=purpose, session_id=sid) == {}

    with pytest.raises(OtpVerifyError) as second:
        await verify_otp(purpose=purpose, session_id=sid, otp_hash=_hash(code, purpose=purpose, sid=sid))
    assert second.value.kind == 'expired_or_used'


async def test_wrong_codes_are_counted_and_the_challenge_dies_at_the_limit(clean_otp) -> None:
    purpose: OtpPurpose = 'signup'
    sid, real = _sid(), '135790'
    clean_otp.append((purpose, sid))
    await _issue(purpose, sid, real, identity='farmer@example.com')

    allowed = get_settings().otp_max_attempts
    wrong = _hash('000001', purpose=purpose, sid=sid)
    for i in range(allowed - 1):
        with pytest.raises(OtpVerifyError) as miss:
            await verify_otp(purpose=purpose, session_id=sid, otp_hash=wrong)
        assert miss.value.kind == 'invalid'
        assert miss.value.attempts_left == allowed - 1 - i

    with pytest.raises(OtpVerifyError) as locked:
        await verify_otp(purpose=purpose, session_id=sid, otp_hash=wrong)
    assert locked.value.kind == 'too_many_attempts'
    # Being locked out must not stay resumable with the code that was really sent.
    with pytest.raises(OtpVerifyError) as gone:
        await verify_otp(purpose=purpose, session_id=sid, otp_hash=_hash(real, purpose=purpose, sid=sid))
    assert gone.value.kind == 'expired_or_used'


async def test_resend_rotates_the_code_and_refuses_a_finished_challenge(clean_otp) -> None:
    purpose: OtpPurpose = 'reset'
    sid, identity = _sid(), 'forgot@example.com'
    clean_otp.append((purpose, sid))
    await _issue(purpose, sid, '111111', identity=identity)
    await resend_otp(
        purpose=purpose, session_id=sid, otp_hash=_hash('222222', purpose=purpose, sid=sid), identity=identity
    )

    with pytest.raises(OtpVerifyError) as stale:
        await verify_otp(purpose=purpose, session_id=sid, otp_hash=_hash('111111', purpose=purpose, sid=sid))
    assert stale.value.kind == 'invalid'
    await _accept(purpose, sid, '222222')

    with pytest.raises(OtpError) as finished:
        await resend_otp(purpose=purpose, session_id=sid, otp_hash='anything', identity=identity)
    assert finished.value.kind == 'expired_or_used'


async def test_drop_otp_clears_the_cooldown_but_not_the_hourly_counter(clean_otp) -> None:
    purpose: OtpPurpose = 'email_change'
    sid, identity = _sid(), 'moving@example.com'
    clean_otp.append((purpose, sid))
    client = get_redis()
    await _issue(purpose, sid, 'h' * 6, identity=identity)
    cooldown_key = otp_cooldown_key(purpose, identity)
    await client.set(cooldown_key, '1', ex=60)

    await drop_otp(purpose=purpose, session_id=sid, identity=identity)

    assert await client.get(otp_key(purpose, sid)) is None
    assert await client.get(cooldown_key) is None


async def test_session_cache_is_keyed_on_the_password_epoch() -> None:
    from app.core.store import read_profile_cache, warm_session_caches

    sid = _sid()
    epoch = '2026-01-01T00:00:00+00:00'
    await warm_session_caches(
        session_id=sid,
        user_id='u-1',
        role='user',
        is_active=True,
        is_email_verified=True,
        pwd_epoch=epoch,
        permissions=('user:read:self',),
        token_version=3,
        profile={'user_id': 'u-1', 'email': 'a@example.com', 'first_name': 'Ada'},
    )
    try:
        cached = await read_session_cache(session_id=sid, pwd_epoch=epoch, token_version=3)
        assert cached is not None
        assert cached['u'] == 'u-1'
        assert cached['t'] == '3'
        assert cached['x'] == ['user:read:self']

        profile = await read_profile_cache(session_id=sid, user_id='u-1')
        assert profile is not None
        assert profile['email'] == 'a@example.com'
        # Wrong owner must not be served (and the blob is dropped).
        assert await read_profile_cache(session_id=sid, user_id='u-other') is None

        # A password change in flight must not be served from cache.
        assert (
            await read_session_cache(session_id=sid, pwd_epoch='2027-01-01T00:00:00+00:00', token_version=3)
            is None
        )
        # Nor may a token minted before a role or permission bump.
        assert await read_session_cache(session_id=sid, pwd_epoch=epoch, token_version=2) is None
    finally:
        await drop_session_cache(session_id=sid)
    assert await read_session_cache(session_id=sid, pwd_epoch=epoch, token_version=3) is None
    assert await read_profile_cache(session_id=sid, user_id='u-1') is None


async def test_fixed_window_limiter_opens_then_closes() -> None:
    from app.core.store import _rate_limit  # the script, not the settings-gated wrapper

    key = f'rl:test:{_sid()}'
    client = get_redis()
    try:
        allowed, window = await _rate_limit([key], [60, 2])
        assert int(allowed) == 1
        assert 0 < int(window) <= 60
        assert int((await _rate_limit([key], [60, 2]))[0]) == 1

        blocked, remaining = await _rate_limit([key], [60, 2])
        assert int(blocked) == 0
        assert 0 < int(remaining) <= 60
    finally:
        await client.delete(key)
