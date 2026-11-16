import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.profile import UserProfile
from app.db.models.roles import Role


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = 'users'

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))  # null for Google-only users
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('roles.id', ondelete='RESTRICT'), nullable=False, index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text('true'), nullable=False
    )
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text('false'), nullable=False
    )

    failed_login_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text('0'), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Bumped whenever every issued token must die at once (admin deactivation,
    # bulk key rotation). Kept in the access token so a stale claim is refused
    # without a denylist lookup.
    token_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text('0'),
        nullable=False,
        comment='Increments to invalidate all access tokens for this account',
    )
    # One-time hash backing POST /auth/restore. Never the plaintext token.
    restore_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    role: Mapped[Role] = relationship(lazy='joined')
    profile: Mapped[UserProfile | None] = relationship(
        back_populates='user', uselist=False, lazy='joined', cascade='all, delete-orphan'
    )

    __table_args__ = (
        CheckConstraint('email = lower(email)', name='email_lowercase'),
        # Soft delete is a lock, not a release: the email stays claimed so a new
        # signup cannot collide with a deactivated account. Restore is the only
        # way back in, which is what keeps email and phone unambiguous.
        UniqueConstraint('email', name='uq_users_email_any'),
        # Named without the ck_users_ prefix: NAMING_CONVENTION adds it, which is
        # exactly the name exceptions.py and the migration refer to.
        CheckConstraint('deleted_at IS NULL OR is_active = false', name='active_not_deleted'),
        # Restore by email: at most one deleted row per address is meaningful.
        Index(
            'ix_users_email_deleted',
            'email',
            unique=True,
            postgresql_where=text('deleted_at IS NOT NULL'),
        ),
    )
