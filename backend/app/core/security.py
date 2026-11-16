import asyncio
import base64
import contextlib
import hashlib
import hmac
import os
import secrets
import string
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

# ---------------------------------------------------------------- passwords

_hasher = PasswordHasher()  # argon2id with safe defaults
# Verified against when the user does not exist, so response time does not reveal it.
_DUMMY_HASH = _hasher.hash('dummy-password-for-timing-equalisation')

COMMON_PASSWORDS = frozenset(
    {
        'password',
        'password1',
        'password123',
        '12345678',
        '123456789',
        'qwerty123',
        'admin123',
        'welcome123',
        'iloveyou1',
        'abc12345',
        'letmein123',
    }
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Same work as a real check, so a missing row is not faster than a wrong password.
        with contextlib.suppress(VerificationError, InvalidHashError):
            _hasher.verify(_DUMMY_HASH, password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


async def hash_password_async(password: str) -> str:
    """argon2 is CPU heavy: run it in a thread so the event loop is not blocked."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str | None) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


def password_policy_errors(password: str, email: str | None = None) -> list[str]:
    s = get_settings()
    errors: list[str] = []
    if len(password) < s.min_password_length:
        errors.append(f'Password must be at least {s.min_password_length} characters')
    if len(password) > s.max_password_length:
        # A 1 MB password is a cheap way to make the server hash it.
        errors.append(f'Password must be at most {s.max_password_length} characters')
    if not any(c.isupper() for c in password):
        errors.append('Password must contain an uppercase letter')
    if not any(c.islower() for c in password):
        errors.append('Password must contain a lowercase letter')
    if not any(c.isdigit() for c in password):
        errors.append('Password must contain a digit')
    if not any(c in string.punctuation for c in password):
        errors.append('Password must contain a special character')
    if password.lower() in COMMON_PASSWORDS:
        errors.append('Password is too common')
    if email:
        local_part = email.split('@')[0].lower()
        if len(local_part) >= 3 and local_part in password.lower():
            errors.append('Password must not contain your email name')
    return errors


# --------------------------------------------------------------------- JWT

ALGORITHM = 'RS256'
JWT_LEEWAY_SECONDS = 10


@lru_cache
def _private_key() -> str:
    return get_settings().read_private_key()


@lru_cache
def _public_key() -> str:
    return get_settings().read_public_key()


def _epoch(value: datetime | None) -> str:
    if value is None:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return str(int(value.timestamp()))


def create_access_token(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    role: str,
    pwd_epoch: datetime | None = None,
    token_version: int = 0,
) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        'sub': str(user_id),
        'sid': str(session_id),
        'role': role,
        'iat': now,
        'exp': now + timedelta(minutes=s.access_token_minutes),
        'jti': uuid.uuid4().hex,
        'iss': s.jwt_issuer,
        'aud': s.jwt_audience,
        # Issuance time: a session older than the last revocation is refused.
        'pcat': int(now.timestamp()),
        # Bumped on every password change: revokes access tokens without a denylist.
        'pwd': _epoch(pwd_epoch),
        # Bumped by an admin for a whole account; both are re-checked on a cache miss.
        'ver': int(token_version),
    }
    return jwt.encode(
        payload,
        _private_key(),
        algorithm=ALGORITHM,
        headers={'kid': s.jwt_key_id},
    )


def decode_access_token(token: str) -> dict:
    s = get_settings()
    try:
        return jwt.decode(
            token,
            _public_key(),
            algorithms=[ALGORITHM],  # pinned: the token can never choose its own algorithm
            audience=s.jwt_audience,
            issuer=s.jwt_issuer,
            leeway=JWT_LEEWAY_SECONDS,
            options={'require': ['exp', 'iat', 'sub', 'sid', 'jti', 'pcat', 'pwd']},
        )
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError('Token expired', code='token_expired') from None
    except jwt.InvalidTokenError:
        raise UnauthorizedError('Invalid token', code='invalid_token') from None


# --------------------------------------------------------------------- OTP

OTP_LENGTH = 6


def generate_otp() -> str:
    return ''.join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


def new_otp_session_id() -> str:
    return secrets.token_urlsafe(32)


def hash_otp(otp: str, *, session_id: str, purpose: str) -> str:
    """HMAC bound to the session and purpose: a login OTP cannot be reused for a reset."""
    message = f'{purpose}:{session_id}:{otp}'.encode()
    return hmac.new(_hmac_key(), message, hashlib.sha256).hexdigest()


def verify_otp(otp: str, *, session_id: str, purpose: str, expected_hash: str) -> bool:
    actual = hash_otp(otp, session_id=session_id, purpose=purpose)
    return hmac.compare_digest(actual, expected_hash)


@lru_cache
def _hmac_key() -> bytes:
    return get_settings().otp_hmac_secret.encode()


@lru_cache
def _payload_key() -> bytes:
    """HKDF(SHA-256) of the OTP secret: a different key than the hashing one."""
    info = b'poultech/otp-transport/v1'
    secret = get_settings().otp_transport_key.encode()
    return _hkdf(secret, info, 32)


def _hkdf(secret: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(b'poultech/hkdf-salt', secret, hashlib.sha256).digest()
    okm = b''
    block = b''
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def encrypt_otp_payload(otp: str, *, session_id: str, purpose: str) -> str:
    """The OTP travels to the worker sealed: Redis holds the broker queue too.

    The plaintext never touches the DB and never touches a log line.
    """
    aad = f'{purpose}:{session_id}'.encode()
    nonce = os.urandom(12)
    sealed = AESGCM(_payload_key()).encrypt(nonce, otp.encode(), aad)
    return base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt_otp_payload(payload: str, *, session_id: str, purpose: str) -> str:
    aad = f'{purpose}:{session_id}'.encode()
    blob = base64.urlsafe_b64decode(payload.encode())
    if len(blob) <= 12:
        raise ValueError('payload too short')
    nonce, sealed = blob[:12], blob[12:]
    return AESGCM(_payload_key()).decrypt(nonce, sealed, aad).decode()


# ------------------------------------------------------------ refresh tokens


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """SHA-256 is right here: the token is already high-entropy. Only this hash is stored."""
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    s = get_settings()
    return datetime.now(UTC) + timedelta(days=s.refresh_token_days)


__all__ = [
    'ALGORITHM',
    'COMMON_PASSWORDS',
    'OTP_LENGTH',
    'create_access_token',
    'decode_access_token',
    'decrypt_otp_payload',
    'encrypt_otp_payload',
    'generate_otp',
    'generate_refresh_token',
    'hash_otp',
    'hash_password',
    'hash_password_async',
    'hash_token',
    'new_otp_session_id',
    'password_needs_rehash',
    'password_policy_errors',
    'refresh_token_expiry',
    'verify_otp',
    'verify_password',
    'verify_password_async',
]
