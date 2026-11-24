from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.mitra.gst import normalize_gstin, normalize_state_code
from app.modules.mitra.gst_schemas import INVOICE_KINDS

_PAY_MODES = frozenset({'cash', 'upi', 'bank', 'cheque', 'credit', 'other'})


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def _money(v: Decimal | float | int) -> Decimal:
    d = Decimal(str(v)).quantize(Decimal('0.01'))
    if d < 0:
        raise ValueError('Amount must be zero or positive')
    return d


class BillingSaleCreateRequest(_Strict):
    invoice_no: str = Field(min_length=1, max_length=64)
    sale_date: date
    flock_batch_ref: str | None = Field(default=None, max_length=64)
    buyer: str = Field(min_length=1, max_length=200)
    item: str = Field(min_length=1, max_length=120)
    quantity: Decimal = Field(gt=0)
    qty_unit: str = Field(min_length=1, max_length=32)
    rate_inr: Decimal = Field(ge=0)
    amount_inr: Decimal | None = None
    pay_mode: str | None = Field(default=None, max_length=32)
    received_inr: Decimal | None = Field(default=None, ge=0)
    balance_inr: Decimal | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=2000)
    invoice_kind: str | None = Field(default=None, max_length=20)
    hsn_sac: str | None = Field(default=None, max_length=12)
    buyer_gstin: str | None = None
    place_of_supply_state: str | None = Field(default=None, max_length=2)
    gst_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    tax_inclusive: bool = False

    @field_validator('pay_mode')
    @classmethod
    def _pay_mode(cls, v: str | None) -> str | None:
        if v is None:
            return None
        key = v.strip().lower()
        if key not in _PAY_MODES:
            raise ValueError('pay_mode must be cash, upi, bank, cheque, credit, or other')
        return key

    @field_validator('invoice_kind')
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        if v is None:
            return None
        key = v.strip().lower()
        if key not in INVOICE_KINDS:
            raise ValueError('invoice_kind must be tax_invoice, bill_of_supply, or receipt')
        return key

    @field_validator('buyer_gstin')
    @classmethod
    def _buyer_gstin(cls, v: str | None) -> str | None:
        return normalize_gstin(v)

    @field_validator('place_of_supply_state')
    @classmethod
    def _pos(cls, v: str | None) -> str | None:
        return normalize_state_code(v)


class BillingSaleUpdateRequest(_Strict):
    invoice_no: str | None = Field(default=None, min_length=1, max_length=64)
    sale_date: date | None = None
    flock_batch_ref: str | None = Field(default=None, max_length=64)
    buyer: str | None = Field(default=None, min_length=1, max_length=200)
    item: str | None = Field(default=None, min_length=1, max_length=120)
    quantity: Decimal | None = Field(default=None, gt=0)
    qty_unit: str | None = Field(default=None, min_length=1, max_length=32)
    rate_inr: Decimal | None = Field(default=None, ge=0)
    amount_inr: Decimal | None = None
    pay_mode: str | None = Field(default=None, max_length=32)
    received_inr: Decimal | None = Field(default=None, ge=0)
    balance_inr: Decimal | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=2000)
    invoice_kind: str | None = Field(default=None, max_length=20)
    hsn_sac: str | None = Field(default=None, max_length=12)
    buyer_gstin: str | None = None
    place_of_supply_state: str | None = Field(default=None, max_length=2)
    gst_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    tax_inclusive: bool | None = None

    @field_validator('pay_mode')
    @classmethod
    def _pay_mode(cls, v: str | None) -> str | None:
        if v is None:
            return None
        key = v.strip().lower()
        if key not in _PAY_MODES:
            raise ValueError('pay_mode must be cash, upi, bank, cheque, credit, or other')
        return key

    @field_validator('invoice_kind')
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        if v is None:
            return None
        key = v.strip().lower()
        if key not in INVOICE_KINDS:
            raise ValueError('invoice_kind must be tax_invoice, bill_of_supply, or receipt')
        return key

    @field_validator('buyer_gstin')
    @classmethod
    def _buyer_gstin(cls, v: str | None) -> str | None:
        return normalize_gstin(v)

    @field_validator('place_of_supply_state')
    @classmethod
    def _pos(cls, v: str | None) -> str | None:
        return normalize_state_code(v)


class BillingSaleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_id: uuid.UUID
    invoice_no: str
    sale_date: date
    flock_batch_ref: str | None
    buyer: str
    item: str
    quantity: Decimal
    qty_unit: str
    rate_inr: Decimal
    amount_inr: Decimal
    pay_mode: str | None
    received_inr: Decimal
    balance_inr: Decimal
    remarks: str | None
    invoice_kind: str
    hsn_sac: str | None
    buyer_gstin: str | None
    place_of_supply_state: str | None
    supply_type: str
    tax_inclusive: bool
    taxable_value_inr: Decimal
    gst_rate_pct: Decimal
    cgst_inr: Decimal
    sgst_inr: Decimal
    igst_inr: Decimal
    total_tax_inr: Decimal
    created_at: datetime
    updated_at: datetime


class BillingSaleListResponse(BaseModel):
    items: list[BillingSaleResponse]
    total: int
    limit: int
    offset: int


class ExpenseCreateRequest(_Strict):
    expense_date: date
    bill_no: str | None = Field(default=None, max_length=64)
    flock_batch_ref: str | None = Field(default=None, max_length=64)
    supplier: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    amount_inr: Decimal = Field(gt=0)
    paid_inr: Decimal | None = Field(default=None, ge=0)
    balance_inr: Decimal | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def _balance(self) -> ExpenseCreateRequest:
        amount = _money(self.amount_inr)
        object.__setattr__(self, 'amount_inr', amount)
        paid = _money(self.paid_inr if self.paid_inr is not None else 0)
        object.__setattr__(self, 'paid_inr', paid)
        balance = self.balance_inr if self.balance_inr is not None else amount - paid
        object.__setattr__(self, 'balance_inr', _money(balance))
        return self


class ExpenseUpdateRequest(_Strict):
    expense_date: date | None = None
    bill_no: str | None = Field(default=None, max_length=64)
    flock_batch_ref: str | None = Field(default=None, max_length=64)
    supplier: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    amount_inr: Decimal | None = Field(default=None, gt=0)
    paid_inr: Decimal | None = Field(default=None, ge=0)
    balance_inr: Decimal | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=2000)


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_id: uuid.UUID
    expense_date: date
    bill_no: str | None
    flock_batch_ref: str | None
    supplier: str
    category: str
    description: str
    amount_inr: Decimal
    paid_inr: Decimal
    balance_inr: Decimal
    remarks: str | None
    created_at: datetime
    updated_at: datetime


class ExpenseListResponse(BaseModel):
    items: list[ExpenseResponse]
    total: int
    limit: int
    offset: int


class ProfitSummaryResponse(BaseModel):
    unit_id: uuid.UUID
    unit_name: str
    farm_type: str
    flock_batch_ref: str | None
    income_inr: Decimal
    expenses_inr: Decimal
    profit_inr: Decimal
    units_sold: Decimal
    profit_per_unit_inr: Decimal | None
    to_receive_inr: Decimal
    to_pay_inr: Decimal


class MitraDashboardFarmCard(BaseModel):
    """One farm row for the owner dashboard (rollups + identity)."""

    model_config = ConfigDict(extra='forbid')

    unit_id: uuid.UUID
    unit_name: str
    farm_type: str
    state: str | None = None
    district: str | None = None
    income_inr: Decimal
    expenses_inr: Decimal
    profit_inr: Decimal
    to_receive_inr: Decimal
    to_pay_inr: Decimal
    units_sold: Decimal
    sale_count: int
    expense_count: int


class MitraDashboardRecentSale(BaseModel):
    """Recent sale line with farm context for the dashboard feed."""

    model_config = ConfigDict(extra='forbid')

    id: uuid.UUID
    unit_id: uuid.UUID
    unit_name: str
    farm_type: str
    invoice_no: str
    sale_date: date
    buyer: str
    item: str
    quantity: Decimal
    qty_unit: str
    amount_inr: Decimal
    received_inr: Decimal
    balance_inr: Decimal


class MitraDashboardTotals(BaseModel):
    model_config = ConfigDict(extra='forbid')

    farm_count: int
    sale_count: int
    expense_count: int
    income_inr: Decimal
    expenses_inr: Decimal
    profit_inr: Decimal
    to_receive_inr: Decimal
    to_pay_inr: Decimal
    units_sold: Decimal


class MitraDashboardResponse(BaseModel):
    """Owner-wide Mitra dashboard: all farms + sales rollups in one response."""

    model_config = ConfigDict(extra='forbid')

    from_date: date | None
    to_date: date | None
    totals: MitraDashboardTotals
    farms: list[MitraDashboardFarmCard]
    recent_sales: list[MitraDashboardRecentSale]

