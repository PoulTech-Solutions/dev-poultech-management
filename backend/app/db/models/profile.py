import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.users import User


class UserProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'user_profiles'

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Stored as +91XXXXXXXXXX. Null until the user completes the profile (e.g. Google signup).
    # Unique: set to NULL when an account is soft-deleted so the number can be reused.
    phone_e164: Mapped[str | None] = mapped_column(String(16), unique=True)
    is_phone_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text('false'), nullable=False
    )

    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    village_city: Mapped[str | None] = mapped_column(String(100))
    district: Mapped[str | None] = mapped_column(String(100))
    tehsil: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(6))
    country: Mapped[str] = mapped_column(String(2), default='IN', server_default=text("'IN'"), nullable=False)

    avatar_url: Mapped[str | None] = mapped_column(String(500))
    preferred_language: Mapped[str] = mapped_column(
        String(10), default='en', server_default=text("'en'"), nullable=False
    )
    region: Mapped[str | None] = mapped_column(String(100))  # used for "rates in your region"
    market_preferred_zone: Mapped[str | None] = mapped_column(String(120))
    is_profile_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text('false'), nullable=False
    )

    @property
    def has_required_fields(self) -> bool:
        """Minimum fields collected at signup (name + Indian mobile)."""
        return bool(self.first_name and self.last_name and self.phone_e164)

    def has_location_for_market(self) -> bool:
        """Enough address to map a NECC hub (pincode preferred; city/state still resolve)."""
        return bool(
            (self.pincode and self.state and self.district)
            or self.village_city
            or self.district
            or self.state
            or self.market_preferred_zone
        )

    user: Mapped['User'] = relationship(back_populates='profile')

    __table_args__ = (
        CheckConstraint("phone_e164 IS NULL OR phone_e164 ~ '^[+]91[6-9][0-9]{9}$'", name='phone_format'),
        CheckConstraint("pincode IS NULL OR pincode ~ '^[1-9][0-9]{5}$'", name='pincode_format'),
    )
