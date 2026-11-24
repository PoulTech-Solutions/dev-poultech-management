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

    dup = await client.post(
        f'/api/v1/mitra/units/{unit_id}/billing',
        headers=bearer(tokens),
        json={
            'invoice_no': 'INV-001',
            'sale_date': '2026-04-02',
            'buyer': 'X',
            'item': 'Eggs',
            'quantity': '1',
            'qty_unit': 'tray',
            'rate_inr': '100',
        },
    )
    assert dup.status_code == 409
    assert dup.json()['error']['code'] == 'mitra_invoice_duplicate'

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
