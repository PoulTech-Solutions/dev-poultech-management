from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.tests.conftest import bearer, login, register_and_verify, tokens_email

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _roles(seeded):
    """Reuse RBAC seed from conftest."""


async def test_geo_catalogs_are_public(client: AsyncClient) -> None:
    states = await client.get('/api/v1/geo/states')
    assert states.status_code == 200, states.text
    assert any(item['name'] == 'Maharashtra' for item in states.json()['items'])
    search = await client.get('/api/v1/geo/pincodes/search', params={'q': '411'})
    assert search.status_code == 200, search.text


async def test_geo_catalogs_list_states_and_districts(client: AsyncClient) -> None:
    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    states = await client.get('/api/v1/geo/states', headers=bearer(tokens))
    assert states.status_code == 200, states.text
    names = [item['name'] for item in states.json()['items']]
    assert 'Maharashtra' in names
    assert 'Delhi' in names

    districts = await client.get(
        '/api/v1/geo/districts',
        params={'state': 'Maharashtra'},
        headers=bearer(tokens),
    )
    assert districts.status_code == 200, districts.text
    assert any(item['name'] == 'Pune' for item in districts.json()['items'])

    pins = await client.get(
        '/api/v1/geo/pincodes',
        params={'state': 'Maharashtra', 'district': 'Pune'},
        headers=bearer(tokens),
    )
    assert pins.status_code == 200, pins.text
    assert any(item['pincode'] == '411001' for item in pins.json()['items'])

    by_name = await client.get(
        '/api/v1/geo/pincodes/search',
        params={'q': 'Pune'},
        headers=bearer(tokens),
    )
    assert by_name.status_code == 200, by_name.text
    assert any(item['district'] == 'Pune' for item in by_name.json()['items'])

    tehsils = await client.get(
        '/api/v1/geo/tehsils',
        params={'state': 'Maharashtra', 'district': 'Pune'},
        headers=bearer(tokens),
    )
    assert tehsils.status_code == 200, tehsils.text
    tehsil_names = [item['name'] for item in tehsils.json()['items']]
    assert 'Pune' in tehsil_names
    assert 'Haveli' in tehsil_names

    offices = await client.get(
        '/api/v1/geo/offices',
        params={'state': 'Maharashtra', 'district': 'Pune'},
        headers=bearer(tokens),
    )
    assert offices.status_code == 200, offices.text
    office_names = [item['name'] for item in offices.json()['items']]
    assert 'Pune GPO' in office_names
    assert any(item['pincode'] == '411001' for item in offices.json()['items'])

    village = await client.get(
        '/api/v1/geo/offices',
        params={'state': 'Maharashtra', 'district': 'Pune', 'q': 'Shivaji'},
        headers=bearer(tokens),
    )
    assert village.status_code == 200, village.text
    assert any(item['name'] == 'Shivajinagar BO' for item in village.json()['items'])


async def test_pincodes_filter_by_tehsil(client: AsyncClient, db) -> None:
    from app.db.models.geo import GeoPincode

    db.add(GeoPincode(pincode='411045', state='Maharashtra', district='Pune', tehsil='Haveli'))
    await db.commit()

    haveli = await client.get(
        '/api/v1/geo/pincodes',
        params={'state': 'Maharashtra', 'district': 'Pune', 'tehsil': 'Haveli'},
    )
    assert haveli.status_code == 200, haveli.text
    haveli_pins = {item['pincode'] for item in haveli.json()['items']}
    assert '411045' in haveli_pins
    assert '411001' in haveli_pins

    pune = await client.get(
        '/api/v1/geo/pincodes',
        params={'state': 'Maharashtra', 'district': 'Pune', 'tehsil': 'Pune'},
    )
    assert pune.status_code == 200, pune.text
    pune_pins = {item['pincode'] for item in pune.json()['items']}
    assert '411001' in pune_pins
    assert '411045' not in pune_pins


async def test_pincode_search_prefix(client: AsyncClient) -> None:
    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    resp = await client.get(
        '/api/v1/geo/pincodes/search',
        params={'q': '411'},
        headers=bearer(tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['items']
    assert any(item['pincode'] == '411001' for item in body['items'])


async def test_profile_rejects_unknown_pincode(client: AsyncClient) -> None:
    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    resp = await client.patch(
        '/api/v1/me/profile',
        headers=bearer(tokens),
        json={'pincode': '199999'},
    )
    assert resp.status_code == 422
    assert resp.json()['error']['code'] == 'invalid_pincode'


async def test_profile_accepts_known_pincode_and_fills_location(client: AsyncClient) -> None:
    await register_and_verify(client)
    tokens = await login(client, tokens_email(client))
    resp = await client.patch(
        '/api/v1/me/profile',
        headers=bearer(tokens),
        json={'pincode': '411001'},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data['pincode'] == '411001'
    assert data['state'] == 'Maharashtra'
    assert data['district'] == 'Pune'
