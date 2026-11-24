from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.auth.schemas import normalise_indian_mobile
from app.modules.mitra.constants import FarmUnitType

_PINCODE_RE = re.compile(r'^[1-9][0-9]{5}$')


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class FarmUnitCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=120)
    farm_type: FarmUnitType
    contact_phone: str
    state: str = Field(min_length=2, max_length=100)
    district: str = Field(min_length=2, max_length=100)
    tehsil: str | None = Field(default=None, max_length=100)
    village_city: str | None = Field(default=None, max_length=100)
    pincode: str | None = None
    address_line1: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=4000)
    hatch_date: date | None = None
    birds_placed: int | None = Field(default=None, ge=0)
    proprietor: str | None = Field(default=None, max_length=200)

    @field_validator('contact_phone')
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_indian_mobile(v)

    @field_validator('pincode')
    @classmethod
    def _pin(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not _PINCODE_RE.fullmatch(v):
            raise ValueError('Pincode must be six digits')
        return v


class FarmUnitUpdateRequest(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    contact_phone: str | None = None
    state: str | None = Field(default=None, min_length=2, max_length=100)
    district: str | None = Field(default=None, min_length=2, max_length=100)
    tehsil: str | None = Field(default=None, max_length=100)
    village_city: str | None = Field(default=None, max_length=100)
    pincode: str | None = None
    address_line1: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=4000)
    hatch_date: date | None = None
    birds_placed: int | None = Field(default=None, ge=0)
    proprietor: str | None = Field(default=None, max_length=200)

    @field_validator('contact_phone')
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalise_indian_mobile(v) if v is not None else None

    @field_validator('pincode')
    @classmethod
    def _pin(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not _PINCODE_RE.fullmatch(v):
            raise ValueError('Pincode must be six digits')
        return v


class FarmUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    farm_type: FarmUnitType
    contact_phone: str
    state: str
    district: str
    tehsil: str | None
    village_city: str | None
    pincode: str | None
    address_line1: str | None
    notes: str | None
    hatch_date: date | None
    birds_placed: int | None
    proprietor: str | None
    created_at: datetime
    updated_at: datetime


class FarmUnitListResponse(BaseModel):
    items: list[FarmUnitResponse]
    total: int
    max_units: int


class DailyRecordUpsertRequest(_Strict):
    record_date: date
    data: dict[str, Any]


class DailyRecordResponse(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    record_date: date
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DailyRecordListResponse(BaseModel):
    items: list[DailyRecordResponse]
    total: int
    limit: int
    offset: int


class HatcheryBatchUpsertRequest(_Strict):
    batch_code: str | None = Field(default=None, min_length=1, max_length=64)
    data: dict[str, Any]


class HatcheryBatchResponse(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    batch_code: str
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class HatcheryBatchListResponse(BaseModel):
    items: list[HatcheryBatchResponse]
    total: int
    limit: int
    offset: int
