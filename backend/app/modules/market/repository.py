from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market import (
    MarketEggDaily,
    MarketEggMonthSummary,
    MarketEggZone,
    MarketPoultryDaily,
    MarketSyncRun,
)
from app.modules.market.zones import normalize_lookup_key


class MarketRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_zone_names(self) -> list[str]:
        stmt = select(MarketEggZone.necc_name).order_by(MarketEggZone.necc_name)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_zone_by_name(self, necc_name: str) -> MarketEggZone | None:
        stmt = select(MarketEggZone).where(MarketEggZone.necc_name == necc_name)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert_zone_rows(
        self,
        rows: list[tuple[str, int, int, dict[int, Decimal], Decimal | None]],
        *,
        year: int,
        month: int,
    ) -> int:
        """Each row: (zone_name, year, month, {day: price}, sheet_average). Returns upsert count."""
        count = 0
        for zone_name, _, _, daily, sheet_average in rows:
            lookup_key = normalize_lookup_key(zone_name)
            zone_stmt = (
                insert(MarketEggZone)
                .values(necc_name=zone_name, lookup_key=lookup_key)
                .on_conflict_do_update(
                    index_elements=[MarketEggZone.necc_name],
                    set_={'lookup_key': lookup_key, 'updated_at': func.now()},
                )
                .returning(MarketEggZone.id)
            )
            zone_id = (await self.db.execute(zone_stmt)).scalar_one()

            for day, price in daily.items():
                rate_date = date(year, month, day)
                stmt = (
                    insert(MarketEggDaily)
                    .values(
                        zone_id=zone_id,
                        rate_date=rate_date,
                        price=price,
                        sheet_year=year,
                        sheet_month=month,
                    )
                    .on_conflict_do_update(
                        index_elements=['zone_id', 'rate_date'],
                        set_={
                            'price': price,
                            'sheet_year': year,
                            'sheet_month': month,
                            'updated_at': func.now(),
                        },
                    )
                )
                await self.db.execute(stmt)
                count += 1
            if daily:
                keep_dates = [date(year, month, day) for day in daily]
                await self.db.execute(
                    delete(MarketEggDaily).where(
                        MarketEggDaily.zone_id == zone_id,
                        MarketEggDaily.sheet_year == year,
                        MarketEggDaily.sheet_month == month,
                        MarketEggDaily.rate_date.notin_(keep_dates),
                    )
                )
                computed = sum(daily.values()) / len(daily)
                summary_stmt = (
                    insert(MarketEggMonthSummary)
                    .values(
                        zone_id=zone_id,
                        sheet_year=year,
                        sheet_month=month,
                        necc_sheet_average=sheet_average,
                        computed_daily_average=computed,
                        days_with_price=len(daily),
                    )
                    .on_conflict_do_update(
                        index_elements=['zone_id', 'sheet_year', 'sheet_month'],
                        set_={
                            'necc_sheet_average': sheet_average,
                            'computed_daily_average': computed,
                            'days_with_price': len(daily),
                            'updated_at': func.now(),
                        },
                    )
                )
                await self.db.execute(summary_stmt)
        return count

    async def month_summary(self, zone_id: uuid.UUID, year: int, month: int) -> MarketEggMonthSummary | None:
        stmt = select(MarketEggMonthSummary).where(
            MarketEggMonthSummary.zone_id == zone_id,
            MarketEggMonthSummary.sheet_year == year,
            MarketEggMonthSummary.sheet_month == month,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def latest_successful_sync(self) -> MarketSyncRun | None:
        stmt = (
            select(MarketSyncRun)
            .where(MarketSyncRun.status == 'ok')
            .order_by(MarketSyncRun.finished_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def begin_sync(self, year: int, month: int) -> MarketSyncRun:
        run = MarketSyncRun(
            sheet_year=year,
            sheet_month=month,
            status='running',
            rows_upserted=0,
            started_at=datetime.now(UTC),
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def finish_sync(self, run: MarketSyncRun, *, status: str, rows: int, error: str | None) -> None:
        run.status = status
        run.rows_upserted = rows
        run.error_message = error
        run.finished_at = datetime.now(UTC)

    async def prices_for_zone_month(self, zone_id: uuid.UUID, year: int, month: int) -> dict[int, Decimal]:
        stmt = select(MarketEggDaily.rate_date, MarketEggDaily.price).where(
            MarketEggDaily.zone_id == zone_id,
            MarketEggDaily.sheet_year == year,
            MarketEggDaily.sheet_month == month,
        )
        rows = (await self.db.execute(stmt)).all()
        return {row.rate_date.day: row.price for row in rows}

    async def price_on_date(self, zone_id: uuid.UUID, rate_date: date) -> Decimal | None:
        stmt = select(MarketEggDaily.price).where(
            MarketEggDaily.zone_id == zone_id,
            MarketEggDaily.rate_date == rate_date,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def monthly_averages(
        self, zone_id: uuid.UUID, *, months: int = 12
    ) -> list[tuple[int, int, Decimal]]:
        stmt = (
            select(MarketEggMonthSummary)
            .where(MarketEggMonthSummary.zone_id == zone_id)
            .order_by(MarketEggMonthSummary.sheet_year.desc(), MarketEggMonthSummary.sheet_month.desc())
            .limit(months)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        rows.reverse()
        result: list[tuple[int, int, Decimal]] = []
        for row in rows:
            value = row.necc_sheet_average or row.computed_daily_average
            if value is not None:
                result.append((row.sheet_year, row.sheet_month, value))
        if result:
            return result
        stmt = (
            select(
                MarketEggDaily.sheet_year,
                MarketEggDaily.sheet_month,
                func.avg(MarketEggDaily.price),
            )
            .where(MarketEggDaily.zone_id == zone_id)
            .group_by(MarketEggDaily.sheet_year, MarketEggDaily.sheet_month)
            .order_by(MarketEggDaily.sheet_year.desc(), MarketEggDaily.sheet_month.desc())
            .limit(months)
        )
        fallback = (await self.db.execute(stmt)).all()
        return [(int(y), int(m), Decimal(str(avg))) for y, m, avg in reversed(fallback)]

    async def monthly_averages_for_year(
        self, zone_id: uuid.UUID, year: int
    ) -> list[tuple[int, int, Decimal]]:
        stmt = (
            select(MarketEggMonthSummary)
            .where(
                MarketEggMonthSummary.zone_id == zone_id,
                MarketEggMonthSummary.sheet_year == year,
            )
            .order_by(MarketEggMonthSummary.sheet_month.asc())
        )
        result: list[tuple[int, int, Decimal]] = []
        for row in (await self.db.execute(stmt)).scalars().all():
            value = row.necc_sheet_average or row.computed_daily_average
            if value is not None:
                result.append((row.sheet_year, row.sheet_month, value))
        if result:
            return result
        stmt = (
            select(
                MarketEggDaily.sheet_year,
                MarketEggDaily.sheet_month,
                func.avg(MarketEggDaily.price),
            )
            .where(
                MarketEggDaily.zone_id == zone_id,
                MarketEggDaily.sheet_year == year,
            )
            .group_by(MarketEggDaily.sheet_year, MarketEggDaily.sheet_month)
            .order_by(MarketEggDaily.sheet_month.asc())
        )
        fallback = (await self.db.execute(stmt)).all()
        return [(int(y), int(m), Decimal(str(avg))) for y, m, avg in fallback]

    async def published_dates_for_zone(
        self, zone_id: uuid.UUID, *, since: date
    ) -> list[tuple[int, int, int]]:
        stmt = (
            select(
                MarketEggDaily.sheet_year,
                MarketEggDaily.sheet_month,
                MarketEggDaily.rate_date,
            )
            .where(
                MarketEggDaily.zone_id == zone_id,
                MarketEggDaily.rate_date >= since,
            )
            .order_by(
                MarketEggDaily.sheet_year.desc(),
                MarketEggDaily.sheet_month.desc(),
                MarketEggDaily.rate_date.asc(),
            )
        )
        rows = (await self.db.execute(stmt)).all()
        return [(int(y), int(m), r.day) for y, m, r in rows]

    async def lock_poultry_channel(self, zone_id: uuid.UUID, product: str) -> None:
        """Transaction-scoped lock so two staff saves cannot insert the same calendar day."""
        await self.db.execute(
            text('SELECT pg_advisory_xact_lock(hashtext(:z), hashtext(:p))'),
            {'z': str(zone_id), 'p': product},
        )

    async def upsert_poultry_daily(
        self,
        *,
        zone_id: uuid.UUID,
        product: str,
        rate_date: date,
        price: Decimal,
        source: str = 'manual',
        updated_by: uuid.UUID | None = None,
    ) -> None:
        stmt = (
            insert(MarketPoultryDaily)
            .values(
                zone_id=zone_id,
                product=product,
                rate_date=rate_date,
                price=price,
                source=source,
                updated_by=updated_by,
            )
            .on_conflict_do_update(
                index_elements=['zone_id', 'product', 'rate_date'],
                set_={
                    'price': price,
                    'source': source,
                    'updated_by': updated_by,
                    'updated_at': func.now(),
                },
            )
        )
        await self.db.execute(stmt)

    async def poultry_price_on_date(
        self, zone_id: uuid.UUID, product: str, rate_date: date
    ) -> Decimal | None:
        stmt = select(MarketPoultryDaily.price).where(
            MarketPoultryDaily.zone_id == zone_id,
            MarketPoultryDaily.product == product,
            MarketPoultryDaily.rate_date == rate_date,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def latest_poultry_on_or_before(
        self, zone_id: uuid.UUID, product: str, rate_date: date
    ) -> tuple[date, Decimal] | None:
        stmt = (
            select(MarketPoultryDaily.rate_date, MarketPoultryDaily.price)
            .where(
                MarketPoultryDaily.zone_id == zone_id,
                MarketPoultryDaily.product == product,
                MarketPoultryDaily.rate_date <= rate_date,
            )
            .order_by(MarketPoultryDaily.rate_date.desc())
            .limit(1)
        )
        row = (await self.db.execute(stmt)).one_or_none()
        if row is None:
            return None
        return row.rate_date, row.price

    async def poultry_prices_for_month(
        self, zone_id: uuid.UUID, product: str, year: int, month: int
    ) -> dict[int, Decimal]:
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        stmt = select(MarketPoultryDaily.rate_date, MarketPoultryDaily.price).where(
            MarketPoultryDaily.zone_id == zone_id,
            MarketPoultryDaily.product == product,
            MarketPoultryDaily.rate_date >= start,
            MarketPoultryDaily.rate_date < end,
        )
        rows = (await self.db.execute(stmt)).all()
        return {row.rate_date.day: row.price for row in rows}

    async def poultry_prices_since(
        self, zone_id: uuid.UUID, product: str, since: date
    ) -> list[tuple[date, Decimal]]:
        stmt = (
            select(MarketPoultryDaily.rate_date, MarketPoultryDaily.price)
            .where(
                MarketPoultryDaily.zone_id == zone_id,
                MarketPoultryDaily.product == product,
                MarketPoultryDaily.rate_date >= since,
            )
            .order_by(MarketPoultryDaily.rate_date)
        )
        rows = (await self.db.execute(stmt)).all()
        return [(row.rate_date, row.price) for row in rows]

    async def prune_stale_sheet(self, year: int, month: int) -> None:
        """Optional cleanup when re-syncing a month (zones removed from NECC)."""
        await self.db.execute(
            delete(MarketEggDaily).where(
                MarketEggDaily.sheet_year == year,
                MarketEggDaily.sheet_month == month,
            )
        )
