"""End-to-end auth: register, verify, sign in, refresh, log out, lock out.

These run against a real Postgres (indexes and constraints are the contract) and
fakeredis (the OTP Lua scripts run unchanged).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.profile import UserProfile
from app.db.models.sessions import UserSession
from app.db.models.users import User
from app.tests.conftest import (
    TEST_PASSWORD,
    TEST_PHONE,
    bearer,
    cookie_value,
    login,
    new_email,
    read_otp,
    register_and_verify,
    relogin,
    tokens_email,
)


async def test_signup_does_not_create_user_until_otp_verify(client, seeded, db):
    email = new_email()
    resp = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': email.upper(),
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
            'phone': TEST_PHONE,
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body['masked_destination'].endswith('@example.com')
    assert '***' in body['masked_destination']
    assert 'otp' not in body
    assert 'otp_session_id' in body

    user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    assert user is None, 'user row is created only after verify-email'


async def test_signup_profile_created_only_after_email_verify(client, seeded, db):
    email = new_email()
    signup = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': email,
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
            'phone': TEST_PHONE,
        },
    )
    assert signup.status_code == 202
    sid = signup.json()['otp_session_id']
    assert (await db.execute(select(User).where(User.email == email))).scalar_one_or_none() is None

    login_before = await client.post(
        '/api/v1/auth/login', json={'email': email, 'password': TEST_PASSWORD}
    )
    assert login_before.status_code == 400
    assert login_before.json()['error']['code'] == 'registration_required'

    otp = await read_otp('signup', sid)
    verified = await client.post(
        '/api/v1/auth/verify-email', json={'otp_session_id': sid, 'otp': otp, 'purpose': 'signup'}
    )
    assert verified.status_code == 200
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one()
    assert profile.first_name == 'Ashish'
    assert profile.phone_e164 is not None
    assert user.is_email_verified is True
    assert user.is_active is True
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
    assert me.json()['is_profile_complete'] is True


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
            'phone': '9123456789',
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
    base = {
        'email': new_email(),
        'first_name': 'Ashish',
        'last_name': 'Kumar',
        'phone': TEST_PHONE,
    }
    resp = await client.post('/api/v1/auth/signup', json={**base, **payload})
    assert resp.status_code == 422, resp.text
    if field == 'confirm_password':
        # a model-level mismatch has no single field, so the message is the contract
        assert any('match' in d['message'] for d in resp.json()['error']['details'])
    else:
        assert any(d['field'].endswith(field) for d in resp.json()['error']['details'])


async def test_login_unknown_email_vs_wrong_password(client, seeded):
    account = await register_and_verify(client)
    unknown = await client.post('/api/v1/auth/login', json={'email': new_email(), 'password': TEST_PASSWORD})
    wrong = await client.post(
        '/api/v1/auth/login', json={'email': account['email'], 'password': 'Wr0ng!Pass9'}
    )
    assert unknown.status_code == wrong.status_code == 400
    assert unknown.json()['error']['code'] == 'registration_required'
    assert wrong.json()['error']['code'] == 'invalid_credentials'
    assert 'could not find an account' in unknown.json()['error']['message'].lower()
    assert 'incorrect' in wrong.json()['error']['message'].lower()


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


async def test_signup_requires_indian_phone(client, seeded):
    resp = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': new_email(),
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
        },
    )
    assert resp.status_code == 422, resp.text


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
            'phone': TEST_PHONE,
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


async def test_resend_otp_signup_and_login(client, seeded):
    email = new_email()
    signup = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': email,
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
            'phone': TEST_PHONE,
        },
    )
    assert signup.status_code == 202
    sid = signup.json()['otp_session_id']
    otp_old = await read_otp('signup', sid)
    resend = await client.post('/api/v1/auth/resend-otp', json={'otp_session_id': sid, 'purpose': 'signup'})
    assert resend.status_code == 200, resend.text
    assert resend.json()['purpose'] == 'signup'
    otp_new = await read_otp('signup', sid)
    assert otp_new != otp_old
    wrong = await client.post(
        '/api/v1/auth/verify-email',
        json={'otp_session_id': sid, 'otp': otp_old, 'purpose': 'signup'},
    )
    assert wrong.status_code == 400
    assert wrong.json()['error']['code'] == 'otp_invalid'
    ok = await client.post(
        '/api/v1/auth/verify-email',
        json={'otp_session_id': sid, 'otp': otp_new, 'purpose': 'signup'},
    )
    assert ok.status_code == 200

    challenge = await client.post('/api/v1/auth/login', json={'email': email, 'password': TEST_PASSWORD})
    assert challenge.status_code == 200
    login_sid = challenge.json()['otp_session_id']
    login_old = await read_otp('login', login_sid)
    login_resend = await client.post(
        '/api/v1/auth/resend-otp', json={'otp_session_id': login_sid, 'purpose': 'login'}
    )
    assert login_resend.status_code == 200
    login_new = await read_otp('login', login_sid)
    assert login_new != login_old


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
            'phone': TEST_PHONE,
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

    client.cookies.clear()
    refresh = await client.post(
        '/api/v1/auth/refresh',
        headers={'X-Poultech-Refresh': '1'},
        json={'refresh_token': tokens['refresh_token']},
    )
    assert refresh.status_code == 401, refresh.text
    assert refresh.json()['error']['code'] in {'session_revoked', 'invalid_refresh', 'missing_refresh'}


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


async def test_leftover_unverified_user_cannot_login_until_otp(client, seeded, db):
    from app.core.security import hash_password_async
    from app.db.models.roles import Role

    email = new_email()
    role = (await db.execute(select(Role).where(Role.name == 'user'))).scalar_one()
    db.add(
        User(
            email=email,
            password_hash=await hash_password_async(TEST_PASSWORD),
            role_id=role.id,
            is_active=True,
            is_email_verified=False,
        )
    )
    await db.commit()

    blocked = await client.post('/api/v1/auth/login', json={'email': email, 'password': TEST_PASSWORD})
    assert blocked.status_code == 400
    assert blocked.json()['error']['code'] == 'email_not_verified'

    signup = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': email,
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Ashish',
            'last_name': 'Kumar',
            'phone': TEST_PHONE,
        },
    )
    assert signup.status_code == 202, signup.text
    sid = signup.json()['otp_session_id']
    otp = await read_otp('signup', sid)
    verified = await client.post(
        '/api/v1/auth/verify-email', json={'otp_session_id': sid, 'otp': otp, 'purpose': 'signup'}
    )
    assert verified.status_code == 200, verified.text
    tokens = await login(client, email)
    assert tokens['access_token']


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
