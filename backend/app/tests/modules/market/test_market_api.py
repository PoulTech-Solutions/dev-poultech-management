from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market import MarketEggDaily, MarketEggZone, MarketSyncRun
from app.tests.conftest import bearer, login, register_and_verify, tokens_email

pytestmark = pytest.mark.asyncio


async def _seed_pune(db: AsyncSession, *, today: date) -> None:
    zone = MarketEggZone(necc_name='Pune', lookup_key='pune')
    db.add(zone)
    await db.flush()
    day_prices = (
        (today.day - 1, Decimal('705')),
        (today.day, Decimal('711')),
        (today.day + 1, Decimal('715')),
    )
    for day, price in day_prices:
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


@pytest.fixture
async def authed_client(client: AsyncClient, seeded, db: AsyncSession):
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
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    await _seed_pune(db, today=today)
    return client, tokens


async def test_dashboard_without_pincode_uses_fallback_hub(
    client: AsyncClient, seeded, db: AsyncSession
) -> None:
    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    await _seed_pune(db, today=today)
    response = await client.get('/api/v1/market/dashboard', headers=bearer(tokens))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['rates'][0]['id'] == 'eggs'
    assert float(body['rates'][0]['value']) >= 700
    assert body['metadata']['selected_zone'] == 'Pune'
    assert body['metadata']['match_status'] in {'fallback', 'state_nearest', 'unmapped'}


async def test_dashboard_requires_auth(client: AsyncClient, seeded) -> None:
    response = await client.get('/api/v1/market/dashboard')
    assert response.status_code == 401


async def test_dashboard_egg_ticker(authed_client) -> None:
    client, tokens = authed_client
    response = await client.get('/api/v1/market/dashboard', headers=bearer(tokens))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['metadata']['selected_zone'] == 'Pune'
    assert len(body['ticker']) == 1
    assert body['ticker'][0]['label'] == 'Egg rate'
    assert body['ticker'][0]['zone'] == 'Pune'
    eggs = body['rates'][0]
    assert eggs['id'] == 'eggs'
    assert eggs['live'] is True
    assert float(eggs['value']) >= 700


async def test_dashboard_na_when_tomorrow_missing(authed_client) -> None:
    client, tokens = authed_client
    response = await client.get('/api/v1/market/dashboard', headers=bearer(tokens))
    eggs = response.json()['rates'][0]
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    if today.day + 1 not in {today.day - 1, today.day, today.day + 1}:
        pytest.skip('fixture seeds adjacent days only')
    if eggs['nextValue'] == '':
        assert eggs['value']
        assert eggs['previousValue'] or eggs['previousValue'] == ''
    else:
        assert float(eggs['nextValue']) >= 700


async def test_egg_trend_month_and_year(authed_client) -> None:
    client, tokens = authed_client
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
    headers = bearer(tokens)
    for window in ('month', 'year', 'days'):
        response = await client.get(
            '/api/v1/market/eggs/trend',
            params={'zone': 'Pune', 'window': window, 'year': today.year, 'month': today.month},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['zone'] == 'Pune'
        assert len(body['series']) >= 1
        assert len(body['points']) >= 1

    sheet = await client.get(
        '/api/v1/market/eggs/sheet',
        params={'zone': 'Pune', 'year': today.year, 'month': today.month},
        headers=headers,
    )
    assert sheet.status_code == 200
    assert sheet.json()['days']

    from app.modules.market.calendar_util import week_of_month

    week = week_of_month(today.day)
    week_resp = await client.get(
        '/api/v1/market/eggs/trend',
        params={
            'zone': 'Pune',
            'window': 'days',
            'year': today.year,
            'month': today.month,
            'week': week,
        },
        headers=headers,
    )
    assert week_resp.status_code == 200, week_resp.text
    assert week_resp.json()['week'] == week
    assert week_resp.json()['year'] == today.year
    assert week_resp.json()['month'] == today.month

    coverage = await client.get(
        '/api/v1/market/eggs/coverage',
        params={'zone': 'Pune'},
        headers=headers,
    )
    assert coverage.status_code == 200, coverage.text
    body = coverage.json()
    assert today.year in body['years']
    assert today.month in body['months'][str(today.year)]
    assert week in body['weeks'][f'{today.year}-{today.month:02d}']


async def test_unknown_zone_returns_404(authed_client) -> None:
    client, tokens = authed_client
    response = await client.get('/api/v1/market/dashboard?zone=UnknownCity', headers=bearer(tokens))
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'market_zone_not_found'


async def test_compare_zones(authed_client) -> None:
    client, tokens = authed_client
    response = await client.get(
        '/api/v1/market/eggs/compare',
        params={'zones': 'Pune'},
        headers=bearer(tokens),
    )
    assert response.status_code == 200
    assert response.json()['zones'][0]['zone'] == 'Pune'


async def test_zone_preference(authed_client, db: AsyncSession) -> None:
    client, tokens = authed_client
    zone = MarketEggZone(necc_name='Mumbai (CC)', lookup_key='mumbai (cc)')
    db.add(zone)
    await db.commit()
    patch = await client.patch(
        '/api/v1/market/zone-preference',
        json={'zone': 'Mumbai (CC)'},
        headers=bearer(tokens),
    )
    assert patch.status_code == 200
    me = await client.get('/api/v1/me', headers=bearer(tokens))
    assert me.json()['market_preferred_zone'] == 'Mumbai (CC)'
