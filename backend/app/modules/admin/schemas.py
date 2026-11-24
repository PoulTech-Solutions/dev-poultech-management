from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.auth.schemas import (
    PurposeType,
    _PasswordMixin,
    normalise_email,
    normalise_indian_mobile,
)

InternalRole = Literal['admin', 'staff']


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class AdminCreateUserRequest(_StrictModel, _PasswordMixin):
    email: EmailStr
    first_name: str = Field(min_length=2, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str
    role: InternalRole

    @field_validator('email')
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)

    @field_validator('phone')
    @classmethod
    def _phone(cls, value: str) -> str:
        return normalise_indian_mobile(value)


class AdminConfirmUserRequest(_StrictModel):
    otp_session_id: str = Field(min_length=16, max_length=64)
    otp: str = Field(pattern=r'^[0-9]{6}$')
    purpose: PurposeType = 'internal_signup'


class AdminUserStatusRequest(_StrictModel):
    is_active: bool


class AdminUserItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    user_id: str
    email: str
    role: str
    first_name: str
    last_name: str
    phone: str | None
    address_line1: str | None = None
    village_city: str | None = None
    tehsil: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    is_active: bool
    is_email_verified: bool
    created_at: str
    last_login_at: str | None


class AdminUserListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    items: list[AdminUserItem]
    total: int
    limit: int
    offset: int
    has_more: bool


class AdminDashboardSaleItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: uuid.UUID
    invoice_no: str
    sale_date: date
    buyer: str
    item: str
    quantity: Decimal
    qty_unit: str
    amount_inr: Decimal
    received_inr: Decimal
    balance_inr: Decimal


class AdminDashboardFarmCard(BaseModel):
    """One farm with owner identity, rollups, and nested sales."""

    model_config = ConfigDict(extra='forbid')

    unit_id: uuid.UUID
    unit_name: str
    farm_type: str
    state: str | None = None
    district: str | None = None
    owner_user_id: uuid.UUID
    owner_email: str
    income_inr: Decimal
    expenses_inr: Decimal
    profit_inr: Decimal
    to_receive_inr: Decimal
    to_pay_inr: Decimal
    units_sold: Decimal
    sale_count: int
    expense_count: int
    sales: list[AdminDashboardSaleItem]


class AdminDashboardTotals(BaseModel):
    model_config = ConfigDict(extra='forbid')

    farm_count: int
    owner_count: int
    sale_count: int
    expense_count: int
    income_inr: Decimal
    expenses_inr: Decimal
    profit_inr: Decimal
    to_receive_inr: Decimal
    to_pay_inr: Decimal
    units_sold: Decimal


class AdminDashboardResponse(BaseModel):
    """Platform-wide Mitra dashboard for admin/super_admin."""

    model_config = ConfigDict(extra='forbid')

    from_date: date | None
    to_date: date | None
    totals: AdminDashboardTotals
    farms: list[AdminDashboardFarmCard]
