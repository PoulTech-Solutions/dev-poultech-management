"""Indian GST helpers — rates and rules come from DB settings, not hardcoded product tax."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from re import compile as re_compile

from app.core.exceptions import AppError

GSTIN_RE = re_compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$')
STATE_CODE_RE = re_compile(r'^[0-9]{2}$')

SupplyType = str  # intra_state | inter_state | exempt


@dataclass(frozen=True, slots=True)
class GstLineTotals:
    taxable_value_inr: Decimal
    gst_rate_pct: Decimal
    cgst_inr: Decimal
    sgst_inr: Decimal
    igst_inr: Decimal
    total_tax_inr: Decimal
    grand_total_inr: Decimal
    supply_type: SupplyType


def normalize_gstin(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    if not v:
        return None
    if not GSTIN_RE.fullmatch(v):
        raise ValueError('GSTIN format is invalid')
    return v


def normalize_state_code(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if not STATE_CODE_RE.fullmatch(v):
        raise ValueError('State code must be two digits (GST state code)')
    return v


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal('0.01'))


def compute_gst_line(
    *,
    line_value_inr: Decimal,
    gst_rate_pct: Decimal,
    seller_state_code: str | None,
    place_of_supply_state: str | None,
    tax_inclusive: bool = False,
    is_gst_registered: bool = False,
) -> GstLineTotals:
    """Split GST for one line. Rates are caller-supplied (from configurable slabs)."""
    rate = _money(Decimal(str(gst_rate_pct)))
    if rate < 0 or rate > 100:
        raise AppError('GST rate must be between 0 and 100', code='mitra_gst_rate_invalid')

    if not is_gst_registered or rate == 0:
        taxable = _money(line_value_inr)
        return GstLineTotals(
            taxable_value_inr=taxable,
            gst_rate_pct=Decimal('0'),
            cgst_inr=Decimal('0'),
            sgst_inr=Decimal('0'),
            igst_inr=Decimal('0'),
            total_tax_inr=Decimal('0'),
            grand_total_inr=taxable,
            supply_type='exempt',
        )

    seller = normalize_state_code(seller_state_code)
    pos = normalize_state_code(place_of_supply_state)
    if not seller or not pos:
        raise AppError(
            'Seller state and place of supply are required for GST invoices',
            code='mitra_gst_state_required',
        )

    if tax_inclusive:
        divisor = Decimal('1') + rate / Decimal('100')
        taxable = _money(line_value_inr / divisor)
    else:
        taxable = _money(line_value_inr)

    tax_total = _money(taxable * rate / Decimal('100'))
    if seller == pos:
        half = _money(tax_total / 2)
        cgst, sgst, igst = half, _money(tax_total - half), Decimal('0')
        supply: SupplyType = 'intra_state'
    else:
        cgst, sgst, igst = Decimal('0'), Decimal('0'), tax_total
        supply = 'inter_state'

    grand = _money(taxable + tax_total)
    return GstLineTotals(
        taxable_value_inr=taxable,
        gst_rate_pct=rate,
        cgst_inr=cgst,
        sgst_inr=sgst,
        igst_inr=igst,
        total_tax_inr=tax_total,
        grand_total_inr=grand,
        supply_type=supply,
    )
