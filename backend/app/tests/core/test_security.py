import uuid

import jwt
import pytest

from app.core import security
from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError


def _token(role: str = 'user') -> str:
    return security.create_access_token(user_id=uuid.uuid4(), session_id=uuid.uuid4(), role=role)


# ------------------------------------------------------------- passwords


def test_hash_and_verify_password():
    hashed = security.hash_password('Str0ng!Pass')
    assert hashed != 'Str0ng!Pass'
    assert security.verify_password('Str0ng!Pass', hashed) is True
    assert security.verify_password('wrong', hashed) is False


def test_same_password_gives_different_hashes():
    assert security.hash_password('Str0ng!Pass') != security.hash_password('Str0ng!Pass')


def test_verify_without_hash_or_with_garbage_is_false():
    assert security.verify_password('anything', None) is False
    assert security.verify_password('anything', 'not-a-hash') is False


async def test_async_hashing():
    hashed = await security.hash_password_async('Str0ng!Pass')
    assert await security.verify_password_async('Str0ng!Pass', hashed) is True


def test_password_policy():
    assert security.password_policy_errors('Str0ng!Pass') == []
    assert security.password_policy_errors('weak')
    assert security.password_policy_errors('alllowercase1!')
    assert security.password_policy_errors('NoDigits!!')
    assert security.password_policy_errors('NoSpecial123')
    assert security.password_policy_errors('Password123')  # common password
    assert security.password_policy_errors('Rahul@2026x', email='rahul@gmail.com')


# ------------------------------------------------------------------- JWT


def test_access_token_roundtrip():
    uid, sid = uuid.uuid4(), uuid.uuid4()
    token = security.create_access_token(user_id=uid, session_id=sid, role='farm_owner')
    claims = security.decode_access_token(token)
    assert claims['sub'] == str(uid)
    assert claims['sid'] == str(sid)
    assert claims['role'] == 'farm_owner'
    assert jwt.get_unverified_header(token)['kid'] == get_settings().jwt_key_id


def test_tampered_token_is_rejected():
    head, body, sig = _token().split('.')
    with pytest.raises(UnauthorizedError):
        security.decode_access_token(f'{head}.{body}x.{sig}')


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(get_settings(), 'access_token_minutes', -5)
    token = _token()
    with pytest.raises(UnauthorizedError) as exc:
        security.decode_access_token(token)
    assert exc.value.code == 'token_expired'


def test_unsigned_token_is_rejected():
    s = get_settings()
    payload = {
        'sub': 'x',
        'sid': 'x',
        'jti': 'x',
        'iat': 1,
        'exp': 9999999999,
        'iss': s.jwt_issuer,
        'aud': s.jwt_audience,
    }
    forged = jwt.encode(payload, key=None, algorithm='none')  # type: ignore[arg-type]
    with pytest.raises(UnauthorizedError):
        security.decode_access_token(forged)


def test_wrong_audience_is_rejected(monkeypatch):
    settings = get_settings()
    original = settings.jwt_audience
    monkeypatch.setattr(settings, 'jwt_audience', 'some-other-service')
    token = _token()
    monkeypatch.setattr(settings, 'jwt_audience', original)
    with pytest.raises(UnauthorizedError):
        security.decode_access_token(token)


def test_garbage_token_is_rejected():
    with pytest.raises(UnauthorizedError):
        security.decode_access_token('not.a.token')


# ------------------------------------------------------------------- OTP


def test_generate_otp_is_six_digits():
    for _ in range(50):
        otp = security.generate_otp()
        assert len(otp) == 6
        assert otp.isdigit()


def test_otp_hash_verification():
    sid = security.new_otp_session_id()
    h = security.hash_otp('123456', session_id=sid, purpose='login')
    assert security.verify_otp('123456', session_id=sid, purpose='login', expected_hash=h)
    assert not security.verify_otp('654321', session_id=sid, purpose='login', expected_hash=h)


def test_otp_is_bound_to_session_and_purpose():
    sid = security.new_otp_session_id()
    h = security.hash_otp('123456', session_id=sid, purpose='login')
    other = security.new_otp_session_id()
    assert not security.verify_otp('123456', session_id=other, purpose='login', expected_hash=h)
    assert not security.verify_otp('123456', session_id=sid, purpose='reset', expected_hash=h)


# --------------------------------------------------------- refresh tokens


def test_refresh_token_and_hash():
    t1, t2 = security.generate_refresh_token(), security.generate_refresh_token()
    assert t1 != t2
    assert security.hash_token(t1) == security.hash_token(t1)
    assert security.hash_token(t1) != t1
    assert len(security.hash_token(t1)) == 64
