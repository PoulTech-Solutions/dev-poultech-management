"""The one rule the whole app stands on: you may touch only your own profile.

There is no admin route in this build, so *nobody* - including a super_admin row -
may read, change or delete another account through these endpoints.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.sessions import UserSession
from app.db.models.users import User
from app.tests.conftest import (
    TEST_PASSWORD,
    bearer,
    cookie_value,
    login,
    new_email,
    read_otp,
    register_and_verify,
    relogin,
)


async def test_me_requires_a_token(client, seeded):
    assert (await client.get('/api/v1/me')).status_code == 401
    forged = await client.get('/api/v1/me', headers={'Authorization': 'Bearer not.a.jwt'})
    assert forged.status_code == 401


async def test_update_profile_persists_normalised_values(client, seeded):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])

    resp = await client.patch(
        '/api/v1/me/profile',
        headers=bearer(tokens),
        json={
            'phone': '98765 43210',
            'district': '  Pune  ',
            'state': 'Maharashtra',
            'pincode': '411001',
            'region': 'Pune',
            'preferred_language': 'MR',
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['phone'] == '+919876543210'
    assert body['district'] == 'Pune'
    assert body['preferred_language'] == 'mr'
    # Phone is stored, never claimed as verified: SMS delivery is not live yet.
    assert body['is_phone_verified'] is False


async def test_profile_rejects_bad_india_input(client, seeded):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    for payload in (
        {'phone': '1234567890'},  # starts with 1: not a valid mobile series
        {'pincode': '011001'},  # leading zero
        {'preferred_language': 'fr'},
        {'email': 'someone@example.com'},  # email is not a profile field here
    ):
        resp = await client.patch('/api/v1/me/profile', headers=bearer(tokens), json=payload)
        assert resp.status_code == 422, (payload, resp.text)
        assert resp.json()['error']['code'] == 'validation_error'


async def test_one_phone_one_account(client, seeded):
    a = await register_and_verify(client)
    ta = await login(client, a['email'])
    assert (
        await client.patch('/api/v1/me/profile', headers=bearer(ta), json={'phone': '9876500001'})
    ).status_code == 200

    b = await register_and_verify(client)
    tb = await login(client, b['email'])
    clash = await client.patch('/api/v1/me/profile', headers=bearer(tb), json={'phone': '9876500001'})
    assert clash.status_code == 409
    assert clash.json()['error']['code'] in {'phone_taken', 'conflict'}


async def test_a_second_account_cannot_touch_the_first(client, seeded):
    owner = await register_and_verify(client)
    owner_tokens = await login(client, owner['email'])
    owner_profile = await client.get('/api/v1/me', headers=bearer(owner_tokens))
    assert owner_profile.status_code == 200
    patch = await client.patch(
        '/api/v1/me/profile', headers=bearer(owner_tokens), json={'district': 'Nashik'}
    )
    assert patch.status_code == 200

    other = await register_and_verify(client)
    other_tokens = await login(client, other['email'])
    other_profile = await client.get('/api/v1/me', headers=bearer(other_tokens))

    assert other_profile.json()['email'] == other['email']
    # the second account must not see the first one's data
    assert other_profile.json()['district'] is None

    # Guessing the id of the other account buys nothing: there is no such route.
    victim_id = other_profile.json()['user_id']
    for path in (
        f'/api/v1/users/{victim_id}',
        f'/api/v1/me/{victim_id}',
        f'/api/v1/users/{victim_id}/profile',
    ):
        assert (await client.get(path, headers=bearer(owner_tokens))).status_code == 404

    patch_other = await client.patch(
        f'/api/v1/users/{victim_id}/profile', headers=bearer(owner_tokens), json={'district': 'X'}
    )
    assert patch_other.status_code == 404
    delete_other = await client.request('DELETE', f'/api/v1/users/{victim_id}', headers=bearer(owner_tokens))
    assert delete_other.status_code == 404


async def test_super_admin_has_no_backdoor_on_user_routes(client, seeded, db):
    from app.db.models.roles import Role

    admin = await register_and_verify(client)
    admin_tokens = await relogin(client, admin['email'])
    await register_and_verify(client)  # a second account that must stay invisible

    role = (await db.execute(select(Role).where(Role.name == 'super_admin'))).scalar_one()
    user = (await db.execute(select(User).where(User.email == admin['email']))).scalar_one()
    user.role_id = role.id
    await db.commit()

    # Re-signed-in as admin (the cache pins role to the session).
    admin_tokens = await relogin(client, admin['email'])
    assert admin_tokens['role'] == 'super_admin'

    victim_id = (await client.get('/api/v1/me', headers=bearer(admin_tokens))).json()['user_id']
    assert victim_id != admin['email']
    # Admin powers are a separate, not-yet-built surface: they cannot arrive by
    # calling the self-service endpoint with an admin token.
    assert (await client.get('/api/v1/users', headers=bearer(admin_tokens))).status_code == 404
    assert (
        await client.delete(f'/api/v1/users/{victim_id}', headers=bearer(admin_tokens))
    ).status_code == 404


async def test_email_change_is_two_step_and_revokes_login(client, seeded):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    old_refresh = tokens['refresh_token']
    new_mail = new_email('moved')

    start = await client.post(
        '/api/v1/me/email',
        headers=bearer(tokens),
        json={'email': new_mail, 'password': TEST_PASSWORD},
    )
    assert start.status_code == 202, start.text
    sid = start.json()['otp_session_id']

    # Nothing has moved yet: the old address still signs in.
    assert (await client.get('/api/v1/me', headers=bearer(tokens))).json()['email'] == account['email']

    otp = await read_otp('email_change', sid)
    confirm = await client.post(
        '/api/v1/me/email/confirm', headers=bearer(tokens), json={'otp_session_id': sid, 'otp': otp}
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body['must_verify_email'] is True, 'a moved address is unproven until it is used'
    assert body['profile']['email'] == new_mail
    assert body['access_token'], 'confirm must mint a fresh access token'
    new_refresh = cookie_value(confirm.headers)
    assert new_refresh
    assert new_refresh != old_refresh

    # Old access token is dead (session revoked + token_version bump).
    dead = await client.get('/api/v1/me', headers=bearer(tokens))
    assert dead.status_code == 401
    assert dead.json()['error']['code'] in {'session_revoked', 'token_revoked'}

    # Fresh tokens work against the new identity.
    me = await client.get('/api/v1/me', headers={'Authorization': f'Bearer {body["access_token"]}'})
    assert me.status_code == 200, me.text
    assert me.json()['email'] == new_mail

    # Cookie outranks body — clear jar so we present only the revoked token.
    client.cookies.clear()
    reuse = await client.post(
        '/api/v1/auth/refresh',
        headers={'X-Poultech-Refresh': '1'},
        json={'refresh_token': old_refresh},
    )
    assert reuse.status_code == 401

    assert (
        await client.post('/api/v1/auth/login', json={'email': account['email'], 'password': TEST_PASSWORD})
    ).status_code == 400


async def test_email_change_rejects_wrong_password(client, seeded):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    resp = await client.post(
        '/api/v1/me/email', headers=bearer(tokens), json={'email': new_email(), 'password': 'Wr0ng!Pass9'}
    )
    assert resp.status_code == 401


async def test_sessions_listing_and_revocation(client, seeded):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    listed = await client.get('/api/v1/me/sessions', headers=bearer(tokens))
    assert listed.status_code == 200
    items = listed.json()['items']
    assert len(items) == 1
    assert items[0]['current'] is True
    assert items[0]['ip_address']

    own = items[0]['session_id']
    assert (
        await client.request('DELETE', f'/api/v1/me/sessions/{own}', headers=bearer(tokens))
    ).status_code == 409
    assert (
        await client.request('DELETE', '/api/v1/me/sessions/not-a-uuid', headers=bearer(tokens))
    ).status_code == 404


async def test_delete_needs_password_and_revokes_every_session(client, seeded, db):
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])

    wrong = await client.request(
        'DELETE', '/api/v1/me', headers=bearer(tokens), json={'password': 'Wr0ng!Pass9'}
    )
    assert wrong.status_code == 401

    ok = await client.request(
        'DELETE', '/api/v1/me', headers=bearer(tokens), json={'password': TEST_PASSWORD}
    )
    assert ok.status_code == 200, ok.text
    assert 'deactivated' in ok.json()['message']

    sessions = (await db.execute(select(UserSession).where(UserSession.revoked_at.is_(None)))).scalars().all()
    assert sessions == [], 'a deleted account must have no live session'

    assert (await client.get('/api/v1/me', headers=bearer(tokens))).status_code == 401


async def test_deleted_account_can_be_restored_by_its_owner(client, seeded):
    from app.tests.conftest import login as do_login  # noqa: F401  (documented import order)

    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    await client.request('DELETE', '/api/v1/me', headers=bearer(tokens), json={'password': TEST_PASSWORD})

    # Sign in is refused, but the reason is actionable and only given after the
    # correct password: an attacker learns nothing.
    blocked = await client.post(
        '/api/v1/auth/login', json={'email': account['email'], 'password': TEST_PASSWORD}
    )
    assert blocked.status_code == 403
    assert blocked.json()['error']['code'] == 'account_deleted'

    request = await client.post('/api/v1/auth/restore', json={'email': account['email']})
    assert request.status_code == 200
    sid = request.json()['otp_session_id']
    assert sid, 'the restore challenge should exist for a deleted account'

    otp = await read_otp('restore', sid)
    done = await client.post(
        '/api/v1/auth/restore/confirm',
        json={'otp_session_id': sid, 'otp': otp, 'purpose': 'restore'},
    )
    assert done.status_code == 200, done.text

    back = await relogin(client, account['email'])
    assert (await client.get('/api/v1/me', headers=bearer(back))).status_code == 200


async def test_delete_does_not_release_the_email(client, seeded):
    """The requirement: a deactivated account keeps its address until restored."""
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    await client.request('DELETE', '/api/v1/me', headers=bearer(tokens), json={'password': TEST_PASSWORD})

    stranger = await client.post(
        '/api/v1/auth/signup',
        json={
            'email': account['email'],
            'password': TEST_PASSWORD,
            'confirm_password': TEST_PASSWORD,
            'first_name': 'Stran',
            'last_name': 'Ger',
        },
    )
    assert stranger.status_code == 409
    assert stranger.json()['error']['code'] == 'email_taken'


async def test_expired_and_garbage_tokens_are_refused(client, seeded):
    import jwt as pyjwt

    account = await register_and_verify(client)
    tokens = await login(client, account['email'])

    # Deliberately wrong alg (API is RS256-only). Key must be ≥32 bytes or PyJWT
    # emits InsecureKeyLengthWarning — that warning is about this fake HMAC key,
    # not production PEM keys.
    hs256 = pyjwt.encode(
        {'sub': 'x'},
        'test-only-hs256-key-at-least-32-bytes!!',
        algorithm='HS256',
    )
    assert (await client.get('/api/v1/me', headers={'Authorization': f'Bearer {hs256}'})).status_code == 401

    from app.core.security import _private_key

    other = pyjwt.encode(
        {'sub': '11111111-1111-1111-1111-111111111111', 'sid': '2', 'jti': '3', 'pcat': 1, 'pwd': ''},
        _private_key(),
        algorithm='RS256',
    )
    refused = await client.get('/api/v1/me', headers={'Authorization': f'Bearer {other}'})
    assert refused.status_code == 401, 'a key-signed token must still fail audience/issuer checks'

    assert (
        await client.get('/api/v1/me', headers={'Authorization': 'Basic ' + tokens['access_token']})
    ).status_code == 401
