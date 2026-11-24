from __future__ import annotations

from decimal import Decimal

from httpx import AsyncClient

from app.modules.mitra.gst import compute_gst_line
from app.tests.conftest import bearer, register_and_verify

_SELLER_GSTIN = '27AACCU9603R1ZM'
_BUYER_GSTIN = '09AABCU9603R1ZK'

_UNIT = {
    'name': 'GST Shed',
    'farm_type': 'broiler',
    'contact_phone': '9876501111',
    'state': 'Maharashtra',
    'district': 'Pune',
}


async def _login(client: AsyncClient) -> dict:
    from app.tests.conftest import login, tokens_email

    await register_and_verify(client)
    return await login(client, tokens_email(client))


def test_compute_gst_intra_vs_inter() -> None:
    intra = compute_gst_line(
        line_value_inr=Decimal('1000'),
        gst_rate_pct=Decimal('5'),
        seller_state_code='27',
        place_of_supply_state='27',
        is_gst_registered=True,
    )
    assert intra.supply_type == 'intra_state'
    assert intra.cgst_inr == Decimal('25.00')
    assert intra.sgst_inr == Decimal('25.00')
    assert intra.igst_inr == Decimal('0.00')
    assert intra.grand_total_inr == Decimal('1050.00')

    inter = compute_gst_line(
        line_value_inr=Decimal('1000'),
        gst_rate_pct=Decimal('5'),
        seller_state_code='27',
        place_of_supply_state='09',
        is_gst_registered=True,
    )
    assert inter.supply_type == 'inter_state'
    assert inter.igst_inr == Decimal('50.00')
    assert inter.cgst_inr == Decimal('0.00')


async def test_mitra_gst_settings_and_taxed_sale(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=_UNIT)
    assert unit.status_code == 201
    unit_id = unit.json()['id']

    settings = await client.put(
        f'/api/v1/mitra/units/{unit_id}/billing-settings',
        headers=bearer(tokens),
        json={
            'is_gst_registered': True,
            'seller_gstin': _SELLER_GSTIN,
            'seller_legal_name': 'Farm GST Pvt',
            'seller_state_code': '27',
            'default_place_of_supply_state': '27',
            'late_payment_interest_pct_annual': '18',
        },
    )
    assert settings.status_code == 200, settings.text
    assert settings.json()['late_payment_interest_pct_annual'] == '18.00'

    slab = await client.post(
        '/api/v1/mitra/gst-rate-slabs',
        headers=bearer(tokens),
        json={'hsn_sac': '0105', 'label': 'Live poultry', 'gst_rate_pct': '5'},
    )
    assert slab.status_code == 201

    sale = await client.post(
        f'/api/v1/mitra/units/{unit_id}/billing',
        headers=bearer(tokens),
        json={
            'invoice_no': 'GST-INTRA-1',
            'sale_date': '2026-05-01',
            'buyer': 'Local buyer',
            'item': 'Broiler',
            'quantity': '100',
            'qty_unit': 'birds',
            'rate_inr': '100',
            'hsn_sac': '0105',
            'place_of_supply_state': '27',
        },
    )
    assert sale.status_code == 201, sale.text
    body = sale.json()
    assert body['supply_type'] == 'intra_state'
    assert Decimal(body['cgst_inr']) == Decimal('250.00')
    assert Decimal(body['sgst_inr']) == Decimal('250.00')
    assert Decimal(body['amount_inr']) == Decimal('10500.00')

    inter = await client.post(
        f'/api/v1/mitra/units/{unit_id}/billing',
        headers=bearer(tokens),
        json={
            'invoice_no': 'GST-INTER-1',
            'sale_date': '2026-05-02',
            'buyer': 'UP buyer',
            'buyer_gstin': _BUYER_GSTIN,
            'item': 'Broiler',
            'quantity': '10',
            'qty_unit': 'birds',
            'rate_inr': '100',
            'hsn_sac': '0105',
            'place_of_supply_state': '09',
        },
    )
    assert inter.status_code == 201, inter.text
    inter_body = inter.json()
    assert inter_body['supply_type'] == 'inter_state'
    assert Decimal(inter_body['igst_inr']) == Decimal('50.00')
    assert Decimal(inter_body['amount_inr']) == Decimal('1050.00')


async def test_mitra_gst_optional_without_registration(client: AsyncClient, seeded) -> None:
    """Sales stay simple receipts until unit enables GST and a positive rate applies."""
    tokens = await _login(client)
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=_UNIT)
    unit_id = unit.json()['id']

    sale = await client.post(
        f'/api/v1/mitra/units/{unit_id}/billing',
        headers=bearer(tokens),
        json={
            'invoice_no': 'RCP-1',
            'sale_date': '2026-05-03',
            'buyer': 'Walk-in',
            'item': 'Eggs',
            'quantity': '10',
            'qty_unit': 'tray',
            'rate_inr': '50',
        },
    )
    assert sale.status_code == 201, sale.text
    body = sale.json()
    assert body['invoice_kind'] == 'receipt'
    assert body['supply_type'] == 'exempt'
    assert Decimal(body['total_tax_inr']) == Decimal('0.00')


async def test_mitra_gst_requires_state_when_taxable(client: AsyncClient, seeded) -> None:
    tokens = await _login(client)
    unit = await client.post('/api/v1/mitra/units', headers=bearer(tokens), json=_UNIT)
    unit_id = unit.json()['id']

    await client.put(
        f'/api/v1/mitra/units/{unit_id}/billing-settings',
        headers=bearer(tokens),
        json={
            'is_gst_registered': True,
            'seller_gstin': _SELLER_GSTIN,
            'seller_state_code': '27',
        },
    )
    await client.post(
        '/api/v1/mitra/gst-rate-slabs',
        headers=bearer(tokens),
        json={'hsn_sac': '0105', 'label': 'Live poultry', 'gst_rate_pct': '5'},
    )

    blocked = await client.post(
        f'/api/v1/mitra/units/{unit_id}/billing',
        headers=bearer(tokens),
        json={
            'invoice_no': 'GST-NO-POS',
            'sale_date': '2026-05-04',
            'buyer': 'Buyer',
            'item': 'Broiler',
            'quantity': '1',
            'qty_unit': 'bird',
            'rate_inr': '100',
            'hsn_sac': '0105',
        },
    )
    assert blocked.status_code == 400
    assert blocked.json()['error']['code'] == 'mitra_gst_state_required'
