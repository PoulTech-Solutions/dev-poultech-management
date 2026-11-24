from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db.models.mitra import MitraBillingSale, MitraDailyRecord, MitraExpense, MitraFarmUnit


def _safe_filename(name: str) -> str:
    base = re.sub(r'[^\w\s-]', '', name, flags=re.UNICODE).strip().replace(' ', '_')
    return (base[:80] or 'farm_unit') + f'_{date.today().isoformat()}'


def _dec(value: Decimal) -> str:
    return format(value, 'f')


def build_csv_export(
    unit: MitraFarmUnit,
    *,
    billing: list[MitraBillingSale],
    expenses: list[MitraExpense],
    daily: list[MitraDailyRecord],
) -> tuple[bytes, str]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['# Unit', unit.name, unit.farm_type])

    writer.writerow([])
    writer.writerow(['## Billing'])
    writer.writerow(
        [
            'invoice_no',
            'sale_date',
            'flock_batch_ref',
            'buyer',
            'item',
            'quantity',
            'qty_unit',
            'rate_inr',
            'amount_inr',
            'pay_mode',
            'received_inr',
            'balance_inr',
            'remarks',
            'invoice_kind',
            'hsn_sac',
            'buyer_gstin',
            'place_of_supply_state',
            'supply_type',
            'tax_inclusive',
            'taxable_value_inr',
            'gst_rate_pct',
            'cgst_inr',
            'sgst_inr',
            'igst_inr',
            'total_tax_inr',
        ]
    )
    for row in billing:
        writer.writerow(
            [
                row.invoice_no,
                row.sale_date.isoformat(),
                row.flock_batch_ref or '',
                row.buyer,
                row.item,
                _dec(row.quantity),
                row.qty_unit,
                _dec(row.rate_inr),
                _dec(row.amount_inr),
                row.pay_mode or '',
                _dec(row.received_inr),
                _dec(row.balance_inr),
                row.remarks or '',
                row.invoice_kind,
                row.hsn_sac or '',
                row.buyer_gstin or '',
                row.place_of_supply_state or '',
                row.supply_type,
                'yes' if row.tax_inclusive else 'no',
                _dec(row.taxable_value_inr),
                _dec(row.gst_rate_pct),
                _dec(row.cgst_inr),
                _dec(row.sgst_inr),
                _dec(row.igst_inr),
                _dec(row.total_tax_inr),
            ]
        )

    writer.writerow([])
    writer.writerow(['## Expenses'])
    writer.writerow(
        [
            'expense_date',
            'bill_no',
            'flock_batch_ref',
            'supplier',
            'category',
            'description',
            'amount_inr',
            'paid_inr',
            'balance_inr',
            'remarks',
        ]
    )
    for row in expenses:
        writer.writerow(
            [
                row.expense_date.isoformat(),
                row.bill_no or '',
                row.flock_batch_ref or '',
                row.supplier,
                row.category,
                row.description,
                _dec(row.amount_inr),
                _dec(row.paid_inr),
                _dec(row.balance_inr),
                row.remarks or '',
            ]
        )

    writer.writerow([])
    writer.writerow(['## Daily records'])
    writer.writerow(['record_date', 'data_json'])
    for row in daily:
        writer.writerow([row.record_date.isoformat(), json.dumps(row.data, separators=(',', ':'))])

    filename = f'{_safe_filename(unit.name)}.csv'
    return buf.getvalue().encode('utf-8-sig'), filename


def build_json_export(
    unit: MitraFarmUnit,
    *,
    billing: list[MitraBillingSale],
    expenses: list[MitraExpense],
    daily: list[MitraDailyRecord],
) -> tuple[bytes, str]:

    def sale_dict(r: MitraBillingSale) -> dict[str, Any]:
        return {
            'invoice_no': r.invoice_no,
            'sale_date': r.sale_date.isoformat(),
            'flock_batch_ref': r.flock_batch_ref,
            'buyer': r.buyer,
            'item': r.item,
            'quantity': str(r.quantity),
            'qty_unit': r.qty_unit,
            'rate_inr': str(r.rate_inr),
            'amount_inr': str(r.amount_inr),
            'pay_mode': r.pay_mode,
            'received_inr': str(r.received_inr),
            'balance_inr': str(r.balance_inr),
            'remarks': r.remarks,
            'invoice_kind': r.invoice_kind,
            'hsn_sac': r.hsn_sac,
            'buyer_gstin': r.buyer_gstin,
            'place_of_supply_state': r.place_of_supply_state,
            'supply_type': r.supply_type,
            'tax_inclusive': r.tax_inclusive,
            'taxable_value_inr': str(r.taxable_value_inr),
            'gst_rate_pct': str(r.gst_rate_pct),
            'cgst_inr': str(r.cgst_inr),
            'sgst_inr': str(r.sgst_inr),
            'igst_inr': str(r.igst_inr),
            'total_tax_inr': str(r.total_tax_inr),
        }

    def exp_dict(r: MitraExpense) -> dict[str, Any]:
        return {
            'expense_date': r.expense_date.isoformat(),
            'bill_no': r.bill_no,
            'flock_batch_ref': r.flock_batch_ref,
            'supplier': r.supplier,
            'category': r.category,
            'description': r.description,
            'amount_inr': str(r.amount_inr),
            'paid_inr': str(r.paid_inr),
            'balance_inr': str(r.balance_inr),
            'remarks': r.remarks,
        }

    payload = {
        'exported_at': datetime.now().astimezone().isoformat(),
        'unit': {'id': str(unit.id), 'name': unit.name, 'farm_type': unit.farm_type},
        'billing': [sale_dict(r) for r in billing],
        'expenses': [exp_dict(r) for r in expenses],
        'daily_records': [
            {'record_date': r.record_date.isoformat(), 'data': r.data} for r in daily
        ],
    }
    filename = f'{_safe_filename(unit.name)}.json'
    return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'), filename
