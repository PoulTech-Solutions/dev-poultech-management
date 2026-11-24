from __future__ import annotations

from decimal import Decimal

from httpx import AsyncClient

from app.tests.conftest import bearer, register_and_verify

_UNIT = {
    'name': 'Billing Shed',
    'farm_type': 'broiler',
    'contact_phone': '9876504321',
    'state': 'Maharashtra',
    'district': 'Pune',
}


async def _login(client: AsyncClient) -> dict:
    from app.tests.conftest import login, tokens_email

    await register_and_verify(client)
    return await login(client, tokens_email(client))


async def test_billing_expense_profit_export(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=_UNIT)
    assert unit.status_code == 201
    unit_id = unit.json()['id']

    sale = await client.post(
        f'/api/v1/mitra/units/{unit_id}/billing',
        headers=bearer(tokens),
        json={
            'invoice_no': 'INV-001',
            'sale_date': '2026-04-01',
            'buyer': 'Trader A',
            'item': 'Broiler live',
            'quantity': '100',
            'qty_unit': 'birds',
            'rate_inr': '150',
            'received_inr': '10000',
        },
    )
    assert sale.status_code == 201, sale.text
    assert Decimal(sale.json()['amount_inr']) == Decimal('15000.00')

    # Same invoice_no = another line item on the same bill (multi-item invoice).
    line2 = await client.post(
        f'/api/v1/mitra/units/{unit_id}/billing',
        headers=bearer(tokens),
        json={
            'invoice_no': 'INV-001',
            'sale_date': '2026-04-01',
            'buyer': 'Trader A',
            'item': 'Eggs',
            'quantity': '1',
            'qty_unit': 'tray',
            'rate_inr': '100',
        },
    )
    assert line2.status_code == 201, line2.text
    assert Decimal(line2.json()['amount_inr']) == Decimal('100.00')

    exp = await client.post(
        f'/api/v1/mitra/units/{unit_id}/expenses',
        headers=bearer(tokens),
        json={
            'expense_date': '2026-04-01',
            'bill_no': 'BILL-9',
            'supplier': 'Feed Co',
            'category': 'feed',
            'description': 'Starter feed',
            'amount_inr': '5000',
            'paid_inr': '2000',
        },
    )
    assert exp.status_code == 201, exp.text

    profit = await client.get(f'/api/v1/mitra/units/{unit_id}/profit-summary', headers=bearer(tokens))
    assert profit.status_code == 200
    body = profit.json()
    assert Decimal(body['income_inr']) == Decimal('15000.00')
    assert Decimal(body['expenses_inr']) == Decimal('5000')
    assert Decimal(body['profit_inr']) == Decimal('10000.00')
    assert Decimal(body['to_receive_inr']) == Decimal('5000.00')
    assert Decimal(body['to_pay_inr']) == Decimal('3000.00')

    export = await client.get(
        f'/api/v1/mitra/units/{unit_id}/export',
        headers=bearer(tokens),
        params={'format': 'csv', 'include_daily': 'false'},
    )
    assert export.status_code == 200
    assert 'attachment' in export.headers.get('content-disposition', '').lower()
    assert b'INV-001' in export.content

    tokens_b = await _login(client)
    blocked = await client.get(f'/api/v1/mitra/units/{unit_id}/billing', headers=bearer(tokens_b))
    assert blocked.status_code == 404


async def test_owner_dashboard_all_farms_and_sales(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    headers = bearer(tokens)

    unit_a = await client.post('/api/v1/mitra/units', headers=headers, json=_UNIT)
    assert unit_a.status_code == 201
    id_a = unit_a.json()['id']

    unit_b = await client.post(
        '/api/v1/mitra/units',
        headers=headers,
        json={**_UNIT, 'name': 'Layer House', 'farm_type': 'layer', 'contact_phone': '9876504322'},
    )
    assert unit_b.status_code == 201
    id_b = unit_b.json()['id']

    sale_a = await client.post(
        f'/api/v1/mitra/units/{id_a}/billing',
        headers=headers,
        json={
            'invoice_no': 'DASH-1',
            'sale_date': '2026-05-01',
            'buyer': 'Buyer A',
            'item': 'Broiler',
            'quantity': '10',
            'qty_unit': 'birds',
            'rate_inr': '100',
            'received_inr': '500',
        },
    )
    assert sale_a.status_code == 201, sale_a.text

    sale_b = await client.post(
        f'/api/v1/mitra/units/{id_b}/billing',
        headers=headers,
        json={
            'invoice_no': 'DASH-2',
            'sale_date': '2026-05-02',
            'buyer': 'Buyer B',
            'item': 'Eggs',
            'quantity': '5',
            'qty_unit': 'tray',
            'rate_inr': '200',
        },
    )
    assert sale_b.status_code == 201, sale_b.text

    exp = await client.post(
        f'/api/v1/mitra/units/{id_a}/expenses',
        headers=headers,
        json={
            'expense_date': '2026-05-01',
            'supplier': 'Feed Co',
            'category': 'feed',
            'description': 'Bag',
            'amount_inr': '300',
            'paid_inr': '100',
        },
    )
    assert exp.status_code == 201, exp.text

    dash = await client.get('/api/v1/mitra/dashboard', headers=headers)
    assert dash.status_code == 200, dash.text
    body = dash.json()
    assert body['totals']['farm_count'] == 2
    assert body['totals']['sale_count'] == 2
    assert body['totals']['expense_count'] == 1
    assert Decimal(body['totals']['income_inr']) == Decimal('2000.00')
    assert Decimal(body['totals']['expenses_inr']) == Decimal('300')
    assert Decimal(body['totals']['profit_inr']) == Decimal('1700.00')
    # DASH-1: 1000 - 500 received; DASH-2: full amount received by default
    assert Decimal(body['totals']['to_receive_inr']) == Decimal('500.00')
    assert Decimal(body['totals']['to_pay_inr']) == Decimal('200')
    assert len(body['farms']) == 2
    assert len(body['recent_sales']) == 2
    assert {s['invoice_no'] for s in body['recent_sales']} == {'DASH-1', 'DASH-2'}

    filtered = await client.get(
        '/api/v1/mitra/dashboard',
        headers=headers,
        params={'from_date': '2026-05-02', 'to_date': '2026-05-02'},
    )
    assert filtered.status_code == 200
    assert filtered.json()['totals']['sale_count'] == 1
    assert Decimal(filtered.json()['totals']['income_inr']) == Decimal('1000.00')
