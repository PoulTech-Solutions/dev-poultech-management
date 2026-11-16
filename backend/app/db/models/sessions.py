import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per login. Its id is the `sid` claim inside the access token."""

    __tablename__ = 'user_sessions'

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )

    # Only SHA-256 hashes are stored, never the real refresh token.
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The token before the last rotation: seeing it again means it was stolen/reused.
    previous_refresh_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))

    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text('now()'), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        # Database-level guarantee: at most one active session per user.
        # If you later allow several devices, drop this index in a new migration.
        Index(
            'uq_user_sessions_one_active',
            'user_id',
            unique=True,
            postgresql_where=text('revoked_at IS NULL'),
        ),
    )
