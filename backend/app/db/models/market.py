from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MarketEggZone(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'market_egg_zones'

    necc_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    lookup_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)

    daily_prices: Mapped[list[MarketEggDaily]] = relationship(back_populates='zone')
    month_summaries: Mapped[list[MarketEggMonthSummary]] = relationship(back_populates='zone')


class MarketEggDaily(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'market_egg_daily'

    zone_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('market_egg_zones.id', ondelete='CASCADE'), nullable=False, index=True
    )
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    # NECC suggested price per 100 eggs (INR)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    sheet_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_month: Mapped[int] = mapped_column(Integer, nullable=False)

    zone: Mapped[MarketEggZone] = relationship(back_populates='daily_prices')

    __table_args__ = (UniqueConstraint('zone_id', 'rate_date', name='uq_market_egg_daily_zone_date'),)


class MarketEggMonthSummary(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """NECC monthly sheet average column + derived stats per zone."""

    __tablename__ = 'market_egg_month_summaries'

    zone_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey('market_egg_zones.id', ondelete='CASCADE'), nullable=False, index=True
    )
    sheet_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_month: Mapped[int] = mapped_column(Integer, nullable=False)
    necc_sheet_average: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    computed_daily_average: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    days_with_price: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    zone: Mapped[MarketEggZone] = relationship(back_populates='month_summaries')

    __table_args__ = (
        UniqueConstraint('zone_id', 'sheet_year', 'sheet_month', name='uq_market_egg_month_zone_period'),
    )


class MarketSyncRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = 'market_sync_runs'

    sheet_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rows_upserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
