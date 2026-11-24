"""Request/response shapes for auth. Validation lives here, not in the service.

Every field that the mockups and the HLD care about (email, phone, pincode,
language) is normalised or rejected on the way in, so no route can skip it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

import phonenumbers
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.core.security import password_policy_errors

PurposeType = Literal[
    'signup', 'login', 'reset', 'change_password', 'email_change', 'restore', 'internal_signup'
]

_PINCODE_RE = re.compile(r'^[1-9][0-9]{5}$')


def _clean(value: str) -> str:
    return value.strip()


_NAME_RE = re.compile(r"^[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F .'-]{1,99}$")


def _clean_name(value: str) -> str:
    value = value.strip()
    if not _NAME_RE.fullmatch(value):
        raise ValueError('Names may contain letters, spaces, apostrophes, dots and hyphens')
    return value


def normalise_email(value: str) -> str:
    """Lowercase to match the ck_users_email_lowercase constraint.

    Gmail dot/plus tricks are deliberately *not* folded: doing so would let one
    person own several accounts that look different on screen. If you want that,
    add a generated column and index it - do not loosen this validator.
    """
    return _clean(value).lower()


def normalise_indian_mobile(value: str) -> str:
    """Accepts 9876543210 / +91 98765 43210 / 098-765-43210, stores E.164."""
    digits = re.sub(r'[^0-9+]', '', _clean(value))
    if digits.startswith('0091'):
        digits = '+' + digits[2:]
    elif digits.startswith('0') and len(digits) == 11:
        digits = '+91' + digits[1:]
    elif len(digits) == 10:
        digits = '+91' + digits
    parsed = phonenumbers.parse(digits, 'IN')
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError('Enter a valid 10-digit Indian mobile number')
    if parsed.country_code != 91:
        raise ValueError('Only Indian (+91) mobile numbers are supported right now')
    national = str(parsed.national_number)
    if len(national) != 10 or national[0] not in '6789':
        # Mirrors ck_user_profiles_phone_format: reject here so the user gets a
        # field error, not a database conflict.
        raise ValueError('Mobile numbers must start with 6, 7, 8 or 9')
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


# max_length is the cheap front-door guard; password_policy_errors() is the real one.
PasswordType = Annotated[str, Field(min_length=8, max_length=128)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class _PasswordMixin(BaseModel):
    password: PasswordType

    @field_validator('password')
    @classmethod
    def _policy(cls, value: str, info) -> str:
        email = None
        data = getattr(info, 'data', None) or {}
        raw = data.get('email')
        if isinstance(raw, str) and '@' in raw:
            email = raw.lower()
        errors = password_policy_errors(value, email)
        if errors:
            raise ValueError('; '.join(errors))
        return value


# ------------------------------------------------------------------- requests


class SignupRequest(_StrictModel, _PasswordMixin):
    email: EmailStr
    first_name: str = Field(min_length=2, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str
    confirm_password: str

    @field_validator('email')
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)

    @field_validator('first_name', 'last_name')
    @classmethod
    def _name(cls, value: str) -> str:
        value = _clean(value)
        if not re.fullmatch(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F .'-]{1,99}", value):
            raise ValueError('Names may contain letters, spaces, apostrophes, dots and hyphens')
        return value

    @field_validator('phone')
    @classmethod
    def _phone(cls, value: str) -> str:
        return normalise_indian_mobile(value)

    @model_validator(mode='after')
    def _match(self) -> SignupRequest:
        if self.password != self.confirm_password:
            raise ValueError('Passwords do not match')
        return self


class LoginRequest(_StrictModel):
    """Login accepts any password string — policy applies only at signup/reset."""

    email: EmailStr
    password: PasswordType
    remember_me: bool = False
    # Set by the client only after showing "already signed in on another device".
    confirm_device_switch: bool = False

    @field_validator('email')
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)


class OtpVerifyRequest(_StrictModel):
    otp_session_id: str = Field(min_length=16, max_length=64)
    otp: str = Field(pattern=r'^[0-9]{6}$')
    purpose: PurposeType = 'login'
    remember_me: bool = False
    confirm_device_switch: bool = False


class OtpResendRequest(_StrictModel):
    otp_session_id: str = Field(min_length=16, max_length=64)
    purpose: PurposeType = 'login'


class GoogleLoginRequest(_StrictModel):
    id_token: str = Field(min_length=20, max_length=4096)


class PasswordChangeRequest(_StrictModel, _PasswordMixin):
    current_password: str = Field(min_length=8, max_length=128)
    confirm_password: str

    @model_validator(mode='after')
    def _match(self) -> PasswordChangeRequest:
        if self.password != self.confirm_password:
            raise ValueError('New passwords do not match')
        if self.current_password == self.password:
            raise ValueError('New password must differ from the current one')
        return self


class PasswordResetRequest(_StrictModel, _PasswordMixin):
    # The session id returned by /auth/forgot-password: it is what binds a code to
    # an account, so the request never has to re-send the email address.
    otp_session_id: str = Field(min_length=16, max_length=64)
    otp: str = Field(pattern=r'^[0-9]{6}$')
    confirm_password: str

    @model_validator(mode='after')
    def _match(self) -> PasswordResetRequest:
        if self.password != self.confirm_password:
            raise ValueError('New passwords do not match')
        return self


class RestoreRequest(_StrictModel):
    """Email only: the owner proves control of the mailbox with the OTP.

    Asking for the old password here would strand anyone who deleted the account
    precisely because they could not remember it.
    """

    email: EmailStr

    @field_validator('email')
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)


# ------------------------------------------------------------------ responses


class OtpChallengeResponse(BaseModel):
    otp_session_id: str
    purpose: str
    expires_in: int
    resend_after: int
    masked_destination: str
    hint: str = 'We sent a 6-digit code to your email.'


class TokenPairResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in: int
    role: str
    must_change_password: bool = False


class SessionInfo(BaseModel):
    session_id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip_address: str | None
    current: bool


class MessageResponse(BaseModel):
    message: str


class ForgotPasswordRequest(_StrictModel):
    email: EmailStr

    @field_validator('email')
    @classmethod
    def _email(cls, value: str) -> str:
        return normalise_email(value)


class RefreshRequest(BaseModel):
    """Body fallback when the httpOnly cookie is missing (Swagger, mobile, tests).

    Do **not** send the access token here — only the refresh token from login
    (``Set-Cookie: poultech_refresh=...`` or the value you stored after verify-otp).
    """

    refresh_token: str | None = Field(
        default=None,
        min_length=32,
        max_length=256,
        description='Refresh token from login/verify-otp. Leave empty if cookie is set.',
    )
