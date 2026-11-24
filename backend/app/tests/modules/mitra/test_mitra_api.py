from __future__ import annotations

from httpx import AsyncClient

from app.modules.mitra.constants import MAX_FARM_UNITS_PER_OWNER
from app.tests.conftest import bearer, register_and_verify

_UNIT = {
    'name': 'Shwet Kamal Layer',
    'farm_type': 'layer',
    'contact_phone': '9876501234',
    'state': 'Maharashtra',
    'district': 'Pune',
    'village_city': 'Shivajinagar',
    'pincode': '411001',
}


async def _login(client: AsyncClient) -> dict:
    from app.tests.conftest import login, tokens_email

    await register_and_verify(client)
    return await login(client, tokens_email(client))


async def test_mitra_unit_crud_and_isolation(client: AsyncClient, seeded) -> None:
    tokens_a = await _login(client)
    create = await client.post('/api/v1/mitra/units', headers=bearer(tokens_a), json=_UNIT)
    assert create.status_code == 201, create.text
    unit_id = create.json()['id']
    assert create.json()['name'] == _UNIT['name']

    listed = await client.get('/api/v1/mitra/units', headers=bearer(tokens_a))
    assert listed.status_code == 200
    assert listed.json()['total'] == 1

    patched = await client.patch(
        f'/api/v1/mitra/units/{unit_id}',
        headers=bearer(tokens_a),
        json={'name': 'Renamed Shed A'},
    )
    assert patched.status_code == 200
    assert patched.json()['name'] == 'Renamed Shed A'

    tokens_b = await _login(client)
    foreign = await client.get(f'/api/v1/mitra/units/{unit_id}', headers=bearer(tokens_b))
    assert foreign.status_code == 404


async def test_mitra_daily_record_upsert(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=_UNIT)
    unit_id = unit.json()['id']
    body = {'record_date': '2026-01-15', 'data': {'mortality': 2, 'egg_numbers': 1200}}
    path = f'/api/v1/mitra/units/{unit_id}/daily-records'
    saved = await client.put(path, headers=bearer(tokens), json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()['data']['egg_numbers'] == 1200

    again = await client.put(
        f'/api/v1/mitra/units/{unit_id}/daily-records',
        headers=bearer(tokens),
        json={'record_date': '2026-01-15', 'data': {'mortality': 3}},
    )
    assert again.status_code == 200
    assert again.json()['data']['mortality'] == 3

    listed = await client.get(f'/api/v1/mitra/units/{unit_id}/daily-records', headers=bearer(tokens))
    assert listed.json()['total'] == 1


async def test_mitra_hatchery_batches_not_daily(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    payload = {**_UNIT, 'name': 'Central Hatch', 'farm_type': 'hatchery'}
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=payload)
    unit_id = unit.json()['id']
    bad_daily = await client.put(
        f'/api/v1/mitra/units/{unit_id}/daily-records',
        headers=bearer(tokens),
        json={'record_date': '2026-01-01', 'data': {}},
    )
    assert bad_daily.status_code == 400

    batch = await client.post(
        f'/api/v1/mitra/units/{unit_id}/hatchery-batches',
        headers=bearer(tokens),
        json={'data': {'batch_code': 'B-001', 'eggs_set': 5000}},
    )
    assert batch.status_code == 201, batch.text
    assert batch.json()['batch_code'] == 'B-001'


async def test_mitra_unit_cap(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    for i in range(MAX_FARM_UNITS_PER_OWNER):
        resp = await client.post(
            '/api/v1/mitra/units',
            headers=bearer(tokens),
            json={**_UNIT, 'name': f'Shed {i}', 'contact_phone': f'9876501{i:03d}'},
        )
        assert resp.status_code == 201, resp.text
    blocked = await client.post(
        '/api/v1/mitra/units',
        headers=bearer(tokens),
        json={**_UNIT, 'name': 'One too many'},
    )
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'mitra_unit_cap'


async def test_mitra_requires_auth(client: AsyncClient, seeded) -> None:
    resp = await client.get('/api/v1/mitra/units')
    assert resp.status_code == 401


async def test_mitra_invalid_daily_payload(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=_UNIT)
    unit_id = unit.json()['id']
    bad = await client.put(
        f'/api/v1/mitra/units/{unit_id}/daily-records',
        headers=bearer(tokens),
        json={'record_date': '2026-02-01', 'data': {'unknown_field': 1}},
    )
    assert bad.status_code == 400
    assert bad.json()['error']['code'] == 'mitra_record_invalid'


async def test_mitra_daily_list_pagination_and_date_range(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=_UNIT)
    unit_id = unit.json()['id']
    base = f'/api/v1/mitra/units/{unit_id}/daily-records'
    for day in ('2026-03-01', '2026-03-02', '2026-03-03'):
        assert (
            await client.put(
                base, headers=bearer(tokens), json={'record_date': day, 'data': {'mortality': 0}}
            )
        ).status_code == 200
    page = await client.get(base, headers=bearer(tokens), params={'limit': 2, 'offset': 0})
    assert page.status_code == 200
    body = page.json()
    assert body['total'] == 3
    assert body['limit'] == 2
    assert len(body['items']) == 2

    bad_range = await client.get(
        base,
        headers=bearer(tokens),
        params={'from_date': '2026-03-10', 'to_date': '2026-03-01'},
    )
    assert bad_range.status_code == 400
    assert bad_range.json()['error']['code'] == 'mitra_invalid_date_range'
