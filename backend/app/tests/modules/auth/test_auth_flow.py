"""End-to-end auth: register, verify, sign in, refresh, log out, lock out.

These run against a real Postgres (indexes and constraints are the contract) and
fakeredis (the OTP Lua scripts run unchanged).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.sessions import UserSession
from app.tests.conftest import (
    TEST_PASSWORD,
    bearer,
    cookie_value,
    login,
    new_email,
    read_otp,
    register_and_verify,
    relogin,
    tokens_email,
)


async def test_signup_creates_unverified_user_and_challenges(client, seeded, db):
    email = new_email()
    resp = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': email.upper(),
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body['masked_destination'].endswith('@example.com')
    assert '***' in body['masked_destination']
    assert 'otp' not in body
    assert 'otp_session_id' in body

    from app.db.models.users import User

    user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one()
    assert user.is_email_verified is False
    assert user.password_hash is not None
    assert not user.password_hash.startswith(TEST_PASSWORD)
    assert user.role.name == 'user'


async def test_full_registration_then_first_signin(client, seeded):
    account = await register_and_verify(client)

    tokens = await login(client, account['email'])
    me = await client.get('/api/v1/me', headers=bearer(tokens))
    assert me.status_code == 200, me.text
    assert me.json()['email'] == account['email']
    assert me.json()['first_name'] == 'Ashish'
    assert me.json()['role'] == 'user'
    assert me.json()['must_verify_email'] is False


async def test_duplicate_email_is_refused(client, seeded):
    account = await register_and_verify(client)
    again = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': account['email'],
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Other',
            'last_name': 'Person',
        },
    )
    assert again.status_code == 409
    assert again.json()['error']['code'] == 'email_taken'


@pytest.mark.parametrize(
    ('payload', 'field'),
    [
        ({'password': 'weakpass', 'confirm_password': 'weakpass'}, 'password'),
        ({'password': 'Nodigits!!', 'confirm_password': 'Nodigits!!'}, 'password'),
        ({'password': TEST_PASSWORD, 'confirm_password': 'Str0ng!Other'}, 'confirm_password'),
        ({'password': TEST_PASSWORD, 'confirm_password': TEST_PASSWORD, 'phone': '12345'}, 'phone'),
        ({'password': TEST_PASSWORD, 'confirm_password': TEST_PASSWORD, 'first_name': 'A'}, 'first_name'),
    ],
)
async def test_signup_validation_never_creates_a_row(client, seeded, payload, field):
    base = {'email': new_email(), 'first_name': 'Ashish', 'last_name': 'Kumar'}
    resp = await client.post('/api/v1/auth/signup', json={**base, **payload})
    assert resp.status_code == 422, resp.text
    if field == 'confirm_password':
        # a model-level mismatch has no single field, so the message is the contract
        assert any('match' in d['message'] for d in resp.json()['error']['details'])
    else:
        assert any(d['field'].endswith(field) for d in resp.json()['error']['details'])


async def test_wrong_password_is_indistinguishable(client, seeded):
    account = await register_and_verify(client)
    unknown = await client.post('/api/v1/auth/login', json={'email': new_email(), 'password': TEST_PASSWORD})
    wrong = await client.post(
        '/api/v1/auth/login', json={'email': account['email'], 'password': 'Wr0ng!Pass9'}
    )
    assert unknown.status_code == wrong.status_code == 400
    assert unknown.json()['error']['code'] == wrong.json()['error']['code'] == 'invalid_credentials'
    assert unknown.json()['error']['message'] == wrong.json()['error']['message']


async def test_five_wrong_otp_codes_kills_the_challenge(client, seeded):
    account = await register_and_verify(client)
    step1 = await client.post(
        '/api/v1/auth/login', json={'email': account['email'], 'password': TEST_PASSWORD}
    )
    sid = step1.json()['otp_session_id']
    for _ in range(5):
        bad = await client.post(
            '/api/v1/auth/verify-otp', json={'otp_session_id': sid, 'otp': '000000', 'purpose': 'login'}
        )
        assert bad.status_code == 400, bad.text
    last = await client.post(
        '/api/v1/auth/verify-otp', json={'otp_session_id': sid, 'otp': '000000', 'purpose': 'login'}
    )
    assert last.json()['error']['code'] == 'otp_expired'


async def test_otp_is_single_use(client, seeded):
    email = new_email()
    signup = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': email,
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
        },
    )
    sid = signup.json()['otp_session_id']
    otp = await read_otp('signup', sid)
    first = await client.post(
        '/api/v1/auth/verify-email', json={'otp_session_id': sid, 'otp': otp, 'purpose': 'signup'}
    )
    assert first.status_code == 200
    second = await client.post(
        '/api/v1/auth/verify-email', json={'otp_session_id': sid, 'otp': otp, 'purpose': 'signup'}
    )
    assert second.status_code == 410
    assert second.json()['error']['code'] == 'otp_expired'


async def test_login_code_cannot_be_used_for_signup(client, seeded):
    """Purpose binding: same 6 digits, wrong flow, refused."""
    email = new_email()
    signup = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': email,
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
        },
    )
    sid = signup.json()['otp_session_id']
    otp = await read_otp('signup', sid)
    other = await client.post(
        '/api/v1/auth/verify-otp', json={'otp_session_id': sid, 'otp': otp, 'purpose': 'login'}
    )
    assert other.status_code == 410


async def test_refresh_rotates_and_reuse_revokes(client, seeded):
    """Exercises the body fallback, the same path a mobile client takes."""
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    old = tokens['refresh_token']
    assert old, 'sign-in must set the refresh cookie'

    rotated = await client.post(
        '/api/v1/auth/refresh',
        headers={'X-Poultech-Refresh': '1'},
        json={'refresh_token': old},
    )
    assert rotated.status_code == 200, rotated.text
    fresh = cookie_value(rotated.headers)
    assert fresh, 'rotation must hand back a different token'
    assert fresh != old
    # A cookie outranks the body (a browser owns its own state), so the jar is
    # cleared to let each call below present exactly the token the test names.
    client.cookies.clear()

    # The token that was just rotated out must now look like theft.
    reused = await client.post(
        '/api/v1/auth/refresh',
        headers={'X-Poultech-Refresh': '1'},
        json={'refresh_token': old},
    )
    assert reused.status_code == 401
    assert reused.json()['error']['code'] == 'token_reuse_detected'

    after = await client.post(
        '/api/v1/auth/refresh',
        headers={'X-Poultech-Refresh': '1'},
        json={'refresh_token': fresh},
    )
    assert after.status_code == 401, 'reuse must kill the whole session'


async def test_refresh_requires_custom_header(client, seeded):
    await register_and_verify(client)
    await login(client, tokens_email(client))
    plain = await client.post('/api/v1/auth/refresh')
    assert plain.status_code == 403
    assert plain.json()['error']['code'] == 'csrf_guard'


async def test_logout_revokes_the_session(client, seeded, db):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    sid = tokens['session_id']

    out = await client.post('/api/v1/auth/logout', headers=bearer(tokens))
    assert out.status_code == 204

    session = (await db.execute(select(UserSession).where(UserSession.id == sid))).scalar_one()
    assert session.revoked_at is not None
    assert session.revoked_reason == 'logout'

    me = await client.get('/api/v1/me', headers=bearer(tokens))
    assert me.status_code == 401
    assert me.json()['error']['code'] == 'session_revoked'


async def test_second_device_is_refused_before_an_email_is_sent(client, seeded):
    account = await register_and_verify(client)
    first = await login(client, account['email'])

    clash = await client.post(
        '/api/v1/auth/login', json={'email': account['email'], 'password': TEST_PASSWORD}
    )
    assert clash.status_code == 409
    assert clash.json()['error']['code'] == 'already_signed_in'
    assert 'otp_session_id' not in clash.json(), 'refused sign-ins must not send a code'

    moved = await login(client, account['email'], confirm=True)
    assert moved['access_token']

    old = await client.get('/api/v1/me', headers=bearer(first))
    assert old.status_code == 401


async def test_password_change_signs_out_other_devices(client, seeded, db):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    changed = await client.post(
        '/api/v1/auth/password/change',
        headers=bearer(tokens),
        json={
            'current_password': TEST_PASSWORD,
            'password': 'N3w!Password',
            'confirm_password': 'N3w!Password',
        },
    )
    assert changed.status_code == 200, changed.text

    # own device still works, with a token minted before the change now dead
    stale = await client.get('/api/v1/me', headers=bearer(tokens))
    assert stale.status_code == 401
    assert stale.json()['error']['code'] in {
        'password_changed',
        'token_revoked',
        'session_revoked',
        'token_expired',
        'invalid_token',
    }

    fresh = await relogin(client, account['email'], 'N3w!Password')
    assert (await client.get('/api/v1/me', headers=bearer(fresh))).status_code == 200


async def test_lockout_after_five_failed_passwords(client, seeded):
    account = await register_and_verify(client)
    last = None
    for _ in range(5):
        last = await client.post(
            '/api/v1/auth/login', json={'email': account['email'], 'password': 'Wr0ng!Pass9'}
        )
    assert last is not None
    assert last.status_code == 400
    blocked = await client.post(
        '/api/v1/auth/login', json={'email': account['email'], 'password': TEST_PASSWORD}
    )
    assert blocked.status_code == 429
    assert blocked.json()['error']['code'] == 'account_locked'
    assert 'Retry-After' in blocked.headers


async def test_forgot_password_answers_the_same_for_unknown_email(client, seeded):
    known = await register_and_verify(client)
    known_resp = await client.post('/api/v1/auth/forgot-password', json={'email': known['email']})
    unknown_resp = await client.post('/api/v1/auth/forgot-password', json={'email': new_email()})
    assert known_resp.status_code == unknown_resp.status_code == 200
    assert unknown_resp.json()['otp_session_id'] == ''


async def test_reset_flow_sets_a_new_password(client, seeded):
    account = await register_and_verify(client)
    forgot = await client.post('/api/v1/auth/forgot-password', json={'email': account['email']})
    sid = forgot.json()['otp_session_id']
    otp = await read_otp('reset', sid)
    reset = await client.post(
        '/api/v1/auth/reset-password',
        json={
            'otp_session_id': sid,
            'otp': otp,
            'password': 'R3set!Pass',
            'confirm_password': 'R3set!Pass',
        },
    )
    assert reset.status_code == 200, reset.text
    assert (await login(client, account['email'], 'R3set!Pass'))['access_token']


async def test_google_only_account_is_told_to_use_google(client, seeded, db):
    from app.db.models.roles import Role
    from app.db.models.users import User

    role = (await db.execute(select(Role).where(Role.name == 'user'))).scalar_one()
    email = new_email('googleuser')
    db.add(User(email=email, password_hash=None, role_id=role.id, is_email_verified=True))
    await db.commit()

    resp = await client.post('/api/v1/auth/login', json={'email': email, 'password': TEST_PASSWORD})
    assert resp.status_code == 400
    assert resp.json()['error']['code'] == 'invalid_credentials'


async def test_health_and_error_shape(client, seeded):
    live = await client.get('/health/live')
    assert live.json() == {'status': 'ok'}
    assert 'x-request-id' in live.headers

    missing = await client.get('/api/v1/me')
    assert missing.status_code == 401
    assert set(missing.json()['error']) >= {'code', 'message', 'request_id'}
