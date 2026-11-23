from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.roles import Role
from app.db.models.users import User
from app.tests.conftest import (
    TEST_PASSWORD,
    bearer,
    login,
    new_email,
    new_phone,
    read_otp,
    register_and_verify,
    relogin,
    tokens_email,
)

pytestmark = pytest.mark.asyncio


async def _promote(db: AsyncSession, email: str, role_name: str) -> None:
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one()
    user.role_id = role.id
    await db.commit()


async def _as_role(client: AsyncClient, db: AsyncSession, role_name: str) -> dict:
    account = await register_and_verify(client)
    await _promote(db, account['email'], role_name)
    tokens = await relogin(client, account['email'])
    assert tokens['role'] == role_name
    return {'email': account['email'], 'tokens': tokens}


def _create_body(role: str) -> dict:
    return {
        'email': new_email(role),
        'password': TEST_PASSWORD,
        'first_name': 'Internal',
        'last_name': 'User',
        'phone': new_phone(),
        'role': role,
    }


async def test_farmer_cannot_list_internal_users(client: AsyncClient, seeded) -> None:
    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    resp = await client.get('/api/v1/admin/users', headers=bearer(tokens))
    assert resp.status_code == 403


async def test_staff_lists_farmers_cannot_create(
    client: AsyncClient, seeded, db: AsyncSession
) -> None:
    farmer = await register_and_verify(client)
    staff = await _as_role(client, db, 'staff')
    listed = await client.get('/api/v1/admin/users', headers=bearer(staff['tokens']))
    assert listed.status_code == 200, listed.text
    items = listed.json()['items']
    emails = {row['email'] for row in items}
    assert farmer['email'] in emails
    assert staff['email'] not in emails
    assert all(row['role'] in {'user', 'farm_owner', 'farm_member'} for row in items)
    farmer_row = next(row for row in items if row['email'] == farmer['email'])
    assert farmer_row['is_email_verified'] is True
    assert 'village_city' in farmer_row
    created = await client.post(
        '/api/v1/admin/users',
        headers=bearer(staff['tokens']),
        json=_create_body('staff'),
    )
    assert created.status_code == 403
    paused = await client.patch(
        f'/api/v1/admin/users/{farmer_row["user_id"]}',
        headers=bearer(staff['tokens']),
        json={'is_active': False},
    )
    assert paused.status_code == 403


async def test_admin_lists_staff_and_farmers_can_deactivate_farmer(
    client: AsyncClient, seeded, db: AsyncSession
) -> None:
    farmer = await register_and_verify(client)
    admin = await _as_role(client, db, 'admin')
    staff_body = _create_body('staff')
    started = await client.post('/api/v1/admin/users', headers=bearer(admin['tokens']), json=staff_body)
    assert started.status_code == 202, started.text
    created = await _confirm_internal(client, admin['tokens'], started.json())
    assert created.status_code == 201, created.text

    listed = await client.get('/api/v1/admin/users', headers=bearer(admin['tokens']))
    assert listed.status_code == 200, listed.text
    by_email = {row['email']: row for row in listed.json()['items']}
    assert farmer['email'] in by_email
    assert staff_body['email'] in by_email
    assert admin['email'] not in by_email
    assert by_email[farmer['email']]['role'] == 'user'
    paused = await client.patch(
        f'/api/v1/admin/users/{by_email[farmer["email"]]["user_id"]}',
        headers=bearer(admin['tokens']),
        json={'is_active': False},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()['is_active'] is False


async def _confirm_internal(client: AsyncClient, tokens: dict, challenge: dict) -> dict:
    sid = challenge['otp_session_id']
    otp = await read_otp('internal_signup', sid)
    created = await client.post(
        '/api/v1/admin/users/verify',
        headers=bearer(tokens),
        json={'otp_session_id': sid, 'otp': otp, 'purpose': 'internal_signup'},
    )
    return created


async def test_admin_creates_staff_not_admin(client: AsyncClient, seeded, db: AsyncSession) -> None:
    admin = await _as_role(client, db, 'admin')
    staff_body = _create_body('staff')
    started = await client.post('/api/v1/admin/users', headers=bearer(admin['tokens']), json=staff_body)
    assert started.status_code == 202, started.text
    assert started.json()['purpose'] == 'internal_signup'
    created = await _confirm_internal(client, admin['tokens'], started.json())
    assert created.status_code == 201, created.text
    assert created.json()['role'] == 'staff'
    assert created.json()['is_email_verified'] is True

    blocked = await client.post(
        '/api/v1/admin/users',
        headers=bearer(admin['tokens']),
        json=_create_body('admin'),
    )
    assert blocked.status_code == 403


async def test_super_admin_creates_admin_and_can_deactivate(
    client: AsyncClient, seeded, db: AsyncSession
) -> None:
    owner = await _as_role(client, db, 'super_admin')
    body = _create_body('admin')
    started = await client.post('/api/v1/admin/users', headers=bearer(owner['tokens']), json=body)
    assert started.status_code == 202, started.text
    created = await _confirm_internal(client, owner['tokens'], started.json())
    assert created.status_code == 201, created.text
    user_id = created.json()['user_id']

    paused = await client.patch(
        f'/api/v1/admin/users/{user_id}',
        headers=bearer(owner['tokens']),
        json={'is_active': False},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()['is_active'] is False

    deleted = await client.delete(f'/api/v1/admin/users/{user_id}', headers=bearer(owner['tokens']))
    assert deleted.status_code == 204


async def test_created_staff_can_login_with_otp(client: AsyncClient, seeded, db: AsyncSession) -> None:
    admin = await _as_role(client, db, 'admin')
    body = _create_body('staff')
    started = await client.post('/api/v1/admin/users', headers=bearer(admin['tokens']), json=body)
    assert started.status_code == 202, started.text
    created = await _confirm_internal(client, admin['tokens'], started.json())
    assert created.status_code == 201, created.text
    tokens = await login(client, body['email'], body['password'], confirm=True)
    assert tokens['role'] == 'staff'


async def test_internal_user_not_created_until_otp(client: AsyncClient, seeded, db: AsyncSession) -> None:
    admin = await _as_role(client, db, 'admin')
    body = _create_body('staff')
    started = await client.post('/api/v1/admin/users', headers=bearer(admin['tokens']), json=body)
    assert started.status_code == 202, started.text
    pending = (await db.execute(select(User).where(User.email == body['email']))).scalar_one_or_none()
    assert pending is None


async def test_cannot_delete_self(client: AsyncClient, seeded, db: AsyncSession) -> None:
    admin = await _as_role(client, db, 'admin')
    me = await client.get('/api/v1/me', headers=bearer(admin['tokens']))
    user_id = me.json()['user_id']
    resp = await client.delete(f'/api/v1/admin/users/{user_id}', headers=bearer(admin['tokens']))
    assert resp.status_code == 403


async def test_directory_list_is_paginated(client: AsyncClient, seeded, db: AsyncSession) -> None:
    for _ in range(3):
        await register_and_verify(client)
    staff = await _as_role(client, db, 'staff')
    page1 = await client.get(
        '/api/v1/admin/users',
        headers=bearer(staff['tokens']),
        params={'limit': 2, 'offset': 0},
    )
    assert page1.status_code == 200, page1.text
    body = page1.json()
    assert body['limit'] == 2
    assert body['offset'] == 0
    assert body['total'] >= 3
    assert len(body['items']) == 2
    assert body['has_more'] is True
    page2 = await client.get(
        '/api/v1/admin/users',
        headers=bearer(staff['tokens']),
        params={'limit': 2, 'offset': 2},
    )
    assert page2.status_code == 200, page2.text
    assert page2.json()['offset'] == 2
    assert len(page2.json()['items']) >= 1
    too_big = await client.get(
        '/api/v1/admin/users',
        headers=bearer(staff['tokens']),
        params={'limit': 51},
    )
    assert too_big.status_code == 422
