import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class OAuthAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'oauth_accounts'

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)  # 'google'
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)  # Google 'sub'
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (UniqueConstraint('provider', 'provider_user_id', name='provider_account'),)
