import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # the backend/ folder


def resolve_path(path: str) -> Path:
    """Relative paths in .env are resolved from the backend/ folder."""
    p = Path(path)
    return p if p.is_absolute() else (BASE_DIR / p).resolve()


def env_file_for(app_env: str) -> Path | None:
    """Pick the settings file for this environment.

    ``.env`` used to be loaded unconditionally, which meant `pytest` silently ran
    the suite - schema drop included - against whatever database the developer had
    configured. ``APP_ENV`` now chooses the file, and a missing per-environment file
    falls back to ``.env`` only for non-test runs.
    """
    override = os.getenv('ENV_FILE')
    if override:
        return resolve_path(override)
    versioned = BASE_DIR / f'.env.{app_env}'
    if versioned.exists():
        return versioned
    default = BASE_DIR / '.env'
    return default if default.exists() and app_env != 'test' else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_file_for(os.getenv('APP_ENV', 'dev')),
        env_file_encoding='utf-8',
        extra='ignore',
        case_sensitive=False,
    )

    app_env: Literal['dev', 'test', 'prod'] = 'dev'
    log_level: str = 'INFO'

    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str

    jwt_private_key_path: str
    jwt_public_key_path: str
    jwt_key_id: str = 'key-1'
    jwt_issuer: str = 'poultech-auth'
    jwt_audience: str = 'poultech-api'
    access_token_minutes: int = 10
    refresh_token_days: int = 7
    refresh_cookie_name: str = 'poultech_refresh'
    refresh_cookie_samesite: Literal['lax', 'strict', 'none'] = 'lax'
    remember_me_extra_days: int = 23
    default_role_name: str = 'user'
    # Reuse of a rotated refresh token used to be accepted inside this window.
    # It is off by default: rotation is immediate, so a stolen token is caught.
    refresh_grace_seconds: int = 0

    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    otp_max_sends_per_hour: int = 5
    otp_hmac_secret: str
    # Auth + profile Redis TTL (sess:{sid} and sess:{sid}:p). Keep short so a
    # missed logout drop cannot outlive this window; DB/cache miss rewarms it.
    session_cache_seconds: int = 30

    login_max_failed_attempts: int = 5
    login_lock_minutes: int = 15
    min_password_length: int = 8
    max_password_length: int = 128

    # EMAIL_BACKEND / SMTP_* / SMTP_FROM_NAME / EMAIL_TAGLINE — from `.env` only.
    # Compose must not hard-override these (Brevo, SES, Gmail, or Mailpit).
    email_backend: Literal['console', 'smtp'] = 'console'
    smtp_host: str = 'localhost'
    smtp_port: int = 587
    smtp_user: str = ''
    smtp_password: str = ''
    smtp_use_tls: bool = True
    smtp_from: str = 'no-reply@poultech.local'
    smtp_from_name: str = 'Poultech Solution'
    email_tagline: str = 'Smart solutions for profitable poultry'

    cors_origins: str = 'http://localhost:5173'
    google_client_id: str = ''
    # Only read X-Forwarded-For when TLS terminates on your own proxy.
    trust_forwarded_for: bool = False

    rate_limit_enabled: bool = True

    # Filled from otp_hmac_secret when not set; used to encrypt OTPs in transit.
    otp_transport_key: str = ''

    login_otp_expiry_notice: int = Field(default=5, ge=1, le=60)

    @model_validator(mode='after')
    def _finalise(self) -> 'Settings':
        if not self.otp_transport_key:
            self.otp_transport_key = self.otp_hmac_secret
        if self.app_env == 'prod':
            if self.email_backend == 'console':
                raise ValueError('EMAIL_BACKEND=console is not allowed in prod')
            if len(self.otp_hmac_secret) < 32:
                raise ValueError('OTP_HMAC_SECRET must be at least 32 characters in prod')
            if not self.google_client_id:
                raise ValueError('GOOGLE_CLIENT_ID is required in prod')
            if self.cors_origins == 'http://localhost:5173':
                raise ValueError('CORS_ORIGINS still points at the dev default in prod')
        return self

    @property
    def is_prod(self) -> bool:
        return self.app_env == 'prod'

    @property
    def is_test(self) -> bool:
        return self.app_env == 'test'

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(',') if o.strip()]

    @property
    def access_token_seconds(self) -> int:
        return self.access_token_minutes * 60

    def read_private_key(self) -> str:
        return resolve_path(self.jwt_private_key_path).read_text()

    def read_public_key(self) -> str:
        return resolve_path(self.jwt_public_key_path).read_text()


@lru_cache
def get_settings() -> Settings:
    return Settings()
