from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.auth.schemas import normalise_email, normalise_indian_mobile

_LANGUAGES = {'en', 'hi', 'mr'}
_NAME_RE = re.compile(r"^[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F .'-]{1,99}$")
_PINCODE_RE = re.compile(r'^[1-9][0-9]{5}$')


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def _clean_name(value: str) -> str:
    value = value.strip()
    if not _NAME_RE.fullmatch(value):
        raise ValueError('Names may contain letters, spaces, apostrophes, dots and hyphens')
    return value


class ProfileResponse(BaseModel):
    """Everything a farmer may see about themselves. Never includes password data."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: EmailStr
    role: str
    first_name: str
    last_name: str
    phone: str | None
    is_phone_verified: bool
    address_line1: str | None
    address_line2: str | None
    village_city: str | None
    district: str | None
    state: str | None
    pincode: str | None
    country: str
    avatar_url: str | None
    preferred_language: str
    region: str | None
    is_profile_complete: bool
    is_email_verified: bool
    must_verify_email: bool
    created_at: str
    last_login_at: str | None


class ProfileUpdateRequest(_StrictModel):
    """Partial update: only the keys you send are touched.

    `email` is deliberately absent - an address change is a two-step verified
    flow (EmailChangeStartRequest) so a hijacked session cannot re-point the
    account's login identity.
    """

    first_name: str | None = Field(default=None, min_length=2, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = None
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    village_city: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    pincode: str | None = None
    preferred_language: str | None = None
    region: str | None = Field(default=None, max_length=100)

    @field_validator('first_name')
    @classmethod
    def _first(cls, v: str | None) -> str | None:
        return _clean_name(v) if v is not None else None

    @field_validator('last_name')
    @classmethod
    def _last(cls, v: str | None) -> str | None:
        return _clean_name(v) if v is not None else None

    @field_validator('phone')
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalise_indian_mobile(v) if v else None

    @field_validator('pincode')
    @classmethod
    def _pin(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not _PINCODE_RE.match(v):
            raise ValueError('Pincode must be 6 digits and cannot start with 0')
        return v

    @field_validator('preferred_language')
    @classmethod
    def _lang(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.lower()
        if v not in _LANGUAGES:
            raise ValueError(f'Language must be one of {sorted(_LANGUAGES)}')
        return v

    @field_validator('district', 'state', 'region', 'village_city', 'address_line1', 'address_line2')
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class EmailChangeStartRequest(_StrictModel):
    """Re-authenticate with the current password, then prove the new address."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator('email')
    @classmethod
    def _email(cls, v: str) -> str:
        return normalise_email(v)


class EmailChangeConfirmRequest(_StrictModel):
    otp_session_id: str = Field(min_length=16, max_length=64)
    otp: str = Field(pattern=r'^[0-9]{6}$')


class AccountDeleteRequest(_StrictModel):
    password: str = Field(min_length=8, max_length=128)
