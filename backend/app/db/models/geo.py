from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GeoPincode(Base):
    """Canonical India Post pincode → state / district (one row per pincode)."""

    __tablename__ = 'geo_pincodes'

    pincode: Mapped[str] = mapped_column(String(6), primary_key=True)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    district: Mapped[str] = mapped_column(String(100), nullable=False)
    tehsil: Mapped[str | None] = mapped_column(String(100))


class GeoOffice(Base):
    """Village / post office that shares a pincode (India Post directory)."""

    __tablename__ = 'geo_offices'

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pincode: Mapped[str] = mapped_column(
        String(6), ForeignKey('geo_pincodes.pincode', ondelete='CASCADE'), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    office_type: Mapped[str | None] = mapped_column(String(20))
    taluk: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (UniqueConstraint('pincode', 'name', name='uq_geo_offices_pincode_name'),)
