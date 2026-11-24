from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.mitra.gst import normalize_gstin, normalize_state_code

INVOICE_KINDS = frozenset({'tax_invoice', 'bill_of_supply', 'receipt'})


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class UnitBillingSettingsUpsert(_Strict):
    is_gst_registered: bool = False
    seller_gstin: str | None = None
    seller_legal_name: str | None = Field(default=None, max_length=200)
    seller_state_code: str | None = Field(default=None, max_length=2)
    default_place_of_supply_state: str | None = Field(default=None, max_length=2)
    invoice_series_prefix: str | None = Field(default=None, max_length=20)
    late_payment_interest_pct_annual: Decimal = Field(default=Decimal('0'), ge=0, le=100)
    default_gst_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)

    @field_validator('seller_gstin')
    @classmethod
    def _gstin(cls, v: str | None) -> str | None:
        return normalize_gstin(v)

    @field_validator('seller_state_code', 'default_place_of_supply_state')
    @classmethod
    def _state(cls, v: str | None) -> str | None:
        return normalize_state_code(v)


class UnitBillingSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_id: uuid.UUID
    is_gst_registered: bool
    seller_gstin: str | None
    seller_legal_name: str | None
    seller_state_code: str | None
    default_place_of_supply_state: str | None
    invoice_series_prefix: str | None
    late_payment_interest_pct_annual: Decimal
    default_gst_rate_pct: Decimal | None
    created_at: datetime
    updated_at: datetime


class GstRateSlabCreate(_Strict):
    hsn_sac: str = Field(min_length=4, max_length=12)
    label: str = Field(min_length=1, max_length=120)
    gst_rate_pct: Decimal = Field(ge=0, le=100)
    effective_from: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class GstRateSlabUpdate(_Strict):
    hsn_sac: str | None = Field(default=None, min_length=4, max_length=12)
    label: str | None = Field(default=None, min_length=1, max_length=120)
    gst_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    effective_from: date | None = None
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


class GstRateSlabResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_user_id: uuid.UUID
    hsn_sac: str
    label: str
    gst_rate_pct: Decimal
    effective_from: date | None
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class GstRateSlabListResponse(BaseModel):
    items: list[GstRateSlabResponse]
    total: int
    limit: int
    offset: int
