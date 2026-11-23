from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market import MarketEggDaily, MarketEggZone, MarketPoultryDaily, MarketSyncRun
from app.db.models.roles import Role
from app.db.models.users import User
from app.tests.conftest import bearer, login, register_and_verify, relogin, tokens_email

pytestmark = pytest.mark.asyncio


async def _seed_pune(db: AsyncSession, *, today: date) -> MarketEggZone:
    zone = MarketEggZone(necc_name='Pune', lookup_key='pune')
    db.add(zone)
    await db.flush()
    for day, price in (
        (today.day - 1, Decimal('705')),
        (today.day, Decimal('711')),
        (today.day + 1, Decimal('715')),
    ):
        if day < 1:
            continue
        db.add(
            MarketEggDaily(
                zone_id=zone.id,
                rate_date=date(today.year, today.month, day),
                price=price,
                sheet_year=today.year,
                sheet_month=today.month,
            )
        )
    db.add(
        MarketSyncRun(
            sheet_year=today.year,
            sheet_month=today.month,
            status='ok',
            rows_upserted=3,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
    )
    await db.commit()
    return zone


@pytest.fixture
async def market_ready(client: AsyncClient, seeded, db: AsyncSession):
    from zoneinfo import ZoneInfo

    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    await client.patch(
        '/api/v1/me/profile',
        json={
            'village_city': 'Pune',
            'state': 'Maharashtra',
            'district': 'Pune',
            'pincode': '411001',
        },
        headers=bearer(tokens),
    )
    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    await _seed_pune(db, today=today)
    return client, tokens, today


async def test_farmer_cannot_upsert_poultry_rates(market_ready) -> None:
    client, tokens, _ = market_ready
    response = await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={'product': 'culls', 'zone': 'Pune', 'today': '45.50'},
        headers=bearer(tokens),
    )
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'permission_required'


async def test_staff_upsert_and_dashboard_includes_poultry(market_ready, db: AsyncSession) -> None:
    client, _tokens, _today = market_ready
    role = (await db.execute(select(Role).where(Role.name == 'staff'))).scalar_one()
    user = (await db.execute(select(User).where(User.email == tokens_email(client)))).scalar_one()
    user.role_id = role.id
    await db.commit()
    staff_tokens = await relogin(client, tokens_email(client))

    upsert = await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={
            'product': 'culls',
            'zone': 'Pune',
            'today': '46.25',
            'yesterday': '45.00',
            'tomorrow': '47.00',
        },
        headers=bearer(staff_tokens),
    )
    assert upsert.status_code == 200, upsert.text
    body = upsert.json()
    assert body['product'] == 'culls'
    assert body['zone'] == 'Pune'
    assert body['source'] == 'manual'

    await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={
            'product': 'broilers',
            'zone': 'Pune',
            'today': '92.50',
            'yesterday': '91.00',
            'tomorrow': '93.00',
        },
        headers=bearer(staff_tokens),
    )

    dash = await client.get('/api/v1/market/dashboard', headers=bearer(staff_tokens))
    assert dash.status_code == 200, dash.text
    rates = {card['id']: card for card in dash.json()['rates']}
    assert 'eggs' in rates
    assert 'culls' in rates
    assert 'broilers' in rates
    assert rates['culls']['source'] == 'manual'
    assert rates['culls']['zone'] == 'Pune'
    assert float(rates['culls']['value']) == pytest.approx(46.25)
    tickers = {t['label'] for t in dash.json()['ticker']}
    assert 'Egg rate' in tickers
    assert 'Culls rate' in tickers
    assert 'Broiler rate' in tickers


async def test_poultry_read_carries_last_published_and_upsert_overwrites(
    market_ready, db: AsyncSession
) -> None:
    from datetime import timedelta

    client, _tokens, today = market_ready
    role = (await db.execute(select(Role).where(Role.name == 'staff'))).scalar_one()
    user = (await db.execute(select(User).where(User.email == tokens_email(client)))).scalar_one()
    user.role_id = role.id
    await db.commit()
    staff_tokens = await relogin(client, tokens_email(client))

    first = await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={'product': 'culls', 'zone': 'Pune', 'today': '40.00'},
        headers=bearer(staff_tokens),
    )
    assert first.status_code == 200, first.text

    older = (await db.execute(select(MarketPoultryDaily))).scalar_one()
    older.rate_date = today - timedelta(days=3)
    await db.commit()

    listed = await client.get(
        '/api/v1/market/admin/poultry-rates',
        params={'product': 'culls', 'zone': 'Pune'},
        headers=bearer(staff_tokens),
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body['today'] == '40'
    assert body['today_carried'] is True
    assert body['today_source_date'] == (today - timedelta(days=3)).isoformat()

    second = await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={'product': 'culls', 'zone': 'Pune', 'today': '41.50'},
        headers=bearer(staff_tokens),
    )
    assert second.status_code == 200, second.text
    assert float(second.json()['today']) == pytest.approx(41.5)
    assert second.json()['today_carried'] is False

    count = (
        await db.execute(
            select(func.count()).select_from(MarketPoultryDaily).where(
                MarketPoultryDaily.product == 'culls',
                MarketPoultryDaily.rate_date == today,
            )
        )
    ).scalar_one()
    assert count == 1

    zones = await client.get('/api/v1/market/zones', headers=bearer(staff_tokens))
    assert zones.status_code == 200
    names = [item['name'] for item in zones.json()['zones']]
    assert 'Pune' in names
    pune = next(item for item in zones.json()['zones'] if item['name'] == 'Pune')
    assert pune['state'] == 'Maharashtra'

    locked = await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={'product': 'culls', 'zone': 'Pune', 'today': '42.00'},
        headers=bearer(staff_tokens),
    )
    assert locked.status_code == 403, locked.text
    assert locked.json()['error']['code'] == 'rate_locked'

    staff_view = await client.get(
        '/api/v1/market/admin/poultry-rates',
        params={'product': 'culls', 'zone': 'Pune'},
        headers=bearer(staff_tokens),
    )
    assert staff_view.json()['today_editable'] is False
    assert staff_view.json()['tomorrow_editable'] is True

    admin_role = (await db.execute(select(Role).where(Role.name == 'admin'))).scalar_one()
    user.role_id = admin_role.id
    await db.commit()
    admin_tokens = await relogin(client, tokens_email(client))
    admin_view = await client.get(
        '/api/v1/market/admin/poultry-rates',
        params={'product': 'culls', 'zone': 'Pune'},
        headers=bearer(admin_tokens),
    )
    assert admin_view.json()['today_editable'] is True
    overwritten = await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={'product': 'culls', 'zone': 'Pune', 'today': '42.00'},
        headers=bearer(admin_tokens),
    )
    assert overwritten.status_code == 200, overwritten.text
    assert float(overwritten.json()['today']) == pytest.approx(42.0)


async def test_concurrent_first_fill_writes_one_row(market_ready, db: AsyncSession) -> None:
    from zoneinfo import ZoneInfo

    client, _tokens, _today = market_ready
    role = (await db.execute(select(Role).where(Role.name == 'staff'))).scalar_one()
    user = (await db.execute(select(User).where(User.email == tokens_email(client)))).scalar_one()
    user.role_id = role.id
    await db.commit()
    staff_tokens = await relogin(client, tokens_email(client))
    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()

    first, second = await asyncio.gather(
        client.put(
            '/api/v1/market/admin/poultry-rates',
            json={'product': 'culls', 'zone': 'Pune', 'today': '10.00'},
            headers=bearer(staff_tokens),
        ),
        client.put(
            '/api/v1/market/admin/poultry-rates',
            json={'product': 'culls', 'zone': 'Pune', 'today': '11.00'},
            headers=bearer(staff_tokens),
        ),
    )
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 403], (first.text, second.text)
    locked = first if first.status_code == 403 else second
    assert locked.json()['error']['code'] == 'rate_locked'
    count = (
        await db.execute(
            select(func.count())
            .select_from(MarketPoultryDaily)
            .where(MarketPoultryDaily.product == 'culls', MarketPoultryDaily.rate_date == today)
        )
    ).scalar_one()
    assert count == 1


async def test_poultry_upsert_requires_at_least_one_day(market_ready, db: AsyncSession) -> None:
    client, _tokens, _today = market_ready
    role = (await db.execute(select(Role).where(Role.name == 'staff'))).scalar_one()
    user = (await db.execute(select(User).where(User.email == tokens_email(client)))).scalar_one()
    user.role_id = role.id
    await db.commit()
    staff_tokens = await relogin(client, tokens_email(client))
    empty = await client.put(
        '/api/v1/market/admin/poultry-rates',
        json={'product': 'culls', 'zone': 'Pune'},
        headers=bearer(staff_tokens),
    )
    assert empty.status_code == 422
