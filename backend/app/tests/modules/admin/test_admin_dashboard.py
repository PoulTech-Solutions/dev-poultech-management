from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.roles import Role
from app.db.models.users import User
from app.tests.conftest import (
    bearer,
    login,
    register_and_verify,
    relogin,
    tokens_email,
)

pytestmark = pytest.mark.asyncio

_UNIT_A = {
    'name': 'Admin Dash Shed A',
    'farm_type': 'broiler',
    'contact_phone': '9876501001',
    'state': 'Maharashtra',
    'district': 'Pune',
}
_UNIT_B = {
    'name': 'Admin Dash Shed B',
    'farm_type': 'layer',
    'contact_phone': '9876501002',
    'state': 'Karnataka',
    'district': 'Bengaluru',
}


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


async def _owner_with_unit_and_sales(
    client: AsyncClient,
    *,
    unit: dict,
    sales: list[dict],
) -> dict:
    account = await register_and_verify(client)
    tokens = await login(client, account['email'])
    headers = bearer(tokens)
    created = await client.post('/api/v1/mitra/units', headers=headers, json=unit)
    assert created.status_code == 201, created.text
    unit_id = created.json()['id']
    for payload in sales:
        resp = await client.post(
            f'/api/v1/mitra/units/{unit_id}/billing', headers=headers, json=payload
        )
        assert resp.status_code == 201, resp.text
    return {'email': account['email'], 'tokens': tokens, 'unit_id': unit_id}


async def test_admin_dashboard_sees_all_owners_farms_and_sales(
    client: AsyncClient, seeded, db: AsyncSession
) -> None:
    owner_a = await _owner_with_unit_and_sales(
        client,
        unit=_UNIT_A,
        sales=[
            {
                'invoice_no': 'ADM-A1',
                'sale_date': '2026-05-01',
                'buyer': 'Buyer A',
                'item': 'Broiler',
                'quantity': '10',
                'qty_unit': 'birds',
                'rate_inr': '100',
                'received_inr': '500',
            },
            {
                'invoice_no': 'ADM-A2',
                'sale_date': '2026-05-03',
                'buyer': 'Buyer A2',
                'item': 'Broiler',
                'quantity': '5',
                'qty_unit': 'birds',
                'rate_inr': '100',
            },
        ],
    )
    owner_b = await _owner_with_unit_and_sales(
        client,
        unit=_UNIT_B,
        sales=[
            {
                'invoice_no': 'ADM-B1',
                'sale_date': '2026-05-02',
                'buyer': 'Buyer B',
                'item': 'Eggs',
                'quantity': '5',
                'qty_unit': 'tray',
                'rate_inr': '200',
            }
        ],
    )

    admin = await _as_role(client, db, 'admin')
    dash = await client.get('/api/v1/admin/dashboard', headers=bearer(admin['tokens']))
    assert dash.status_code == 200, dash.text
    body = dash.json()

    unit_ids = {f['unit_id'] for f in body['farms']}
    assert owner_a['unit_id'] in unit_ids
    assert owner_b['unit_id'] in unit_ids
    assert body['totals']['owner_count'] >= 2
    assert body['totals']['farm_count'] >= 2
    assert body['totals']['sale_count'] >= 3

    by_unit = {f['unit_id']: f for f in body['farms']}
    farm_a = by_unit[owner_a['unit_id']]
    farm_b = by_unit[owner_b['unit_id']]
    assert farm_a['owner_email'] == owner_a['email']
    assert farm_b['owner_email'] == owner_b['email']
    assert {s['invoice_no'] for s in farm_a['sales']} == {'ADM-A1', 'ADM-A2'}
    assert {s['invoice_no'] for s in farm_b['sales']} == {'ADM-B1'}
    assert Decimal(farm_a['income_inr']) == Decimal('1500.00')
    assert Decimal(farm_b['income_inr']) == Decimal('1000.00')


async def test_farm_owner_cannot_access_admin_dashboard(client: AsyncClient, seeded) -> None:
    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    resp = await client.get('/api/v1/admin/dashboard', headers=bearer(tokens))
    assert resp.status_code == 403


async def test_admin_dashboard_date_filter_and_sales_limit(
    client: AsyncClient, seeded, db: AsyncSession
) -> None:
    owner = await _owner_with_unit_and_sales(
        client,
        unit=_UNIT_A,
        sales=[
            {
                'invoice_no': 'FLT-1',
                'sale_date': '2026-05-01',
                'buyer': 'Early',
                'item': 'Broiler',
                'quantity': '1',
                'qty_unit': 'birds',
                'rate_inr': '100',
            },
            {
                'invoice_no': 'FLT-2',
                'sale_date': '2026-05-02',
                'buyer': 'Mid',
                'item': 'Broiler',
                'quantity': '1',
                'qty_unit': 'birds',
                'rate_inr': '100',
            },
            {
                'invoice_no': 'FLT-3',
                'sale_date': '2026-05-03',
                'buyer': 'Late',
                'item': 'Broiler',
                'quantity': '1',
                'qty_unit': 'birds',
                'rate_inr': '100',
            },
        ],
    )
    admin = await _as_role(client, db, 'admin')
    headers = bearer(admin['tokens'])

    filtered = await client.get(
        '/api/v1/admin/dashboard',
        headers=headers,
        params={'from_date': '2026-05-02', 'to_date': '2026-05-02'},
    )
    assert filtered.status_code == 200, filtered.text
    farm = next(f for f in filtered.json()['farms'] if f['unit_id'] == owner['unit_id'])
    assert farm['sale_count'] == 1
    assert {s['invoice_no'] for s in farm['sales']} == {'FLT-2'}
    assert Decimal(farm['income_inr']) == Decimal('100.00')

    limited = await client.get(
        '/api/v1/admin/dashboard',
        headers=headers,
        params={'sales_limit': 1},
    )
    assert limited.status_code == 200, limited.text
    farm_limited = next(f for f in limited.json()['farms'] if f['unit_id'] == owner['unit_id'])
    assert farm_limited['sale_count'] == 3
    assert len(farm_limited['sales']) == 1
    assert farm_limited['sales'][0]['invoice_no'] == 'FLT-3'
