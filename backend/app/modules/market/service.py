from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.market_errors import MarketDataUnavailableError, ProfileLocationRequiredError
from app.core.redis_conn import get_redis
from app.db.models.users import User
from app.modules.market.calendar_util import list_recent_months
from app.modules.market.necc_client import fetch_necc_sheet_html, parse_necc_html
from app.modules.market.pricing_util import neighbor_on_sheet, spot_from_sheet
from app.modules.market.repository import MarketRepository
from app.modules.market.schemas import (
    MarketCompareResponse,
    MarketDashboardMetadata,
    MarketDashboardResponse,
    MarketDayPrice,
    MarketEggSheetResponse,
    MarketRateCard,
    MarketTickerItem,
    MarketTrendPoint,
    MarketTrendResponse,
    MarketZoneItem,
    MarketZonesResponse,
)
from app.modules.market.zones import (
    DEFAULT_FALLBACK_ZONE,
    resolve_zone_name,
    validate_zone_or_raise,
)
from app.modules.users.repository import UsersRepository

log = structlog.get_logger()


def _decimal_str(value: Decimal | float) -> str:
    if isinstance(value, Decimal):
        rounded = value.quantize(Decimal('0.01'))
        return str(int(rounded)) if rounded == rounded.to_integral_value() else str(rounded)
    return str(round(float(value), 2))


def _today_ist() -> date:
    return datetime.now(ZoneInfo('Asia/Kolkata')).date()


class MarketService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._repo = MarketRepository(db)
        self._users = UsersRepository(db)

    async def sync_necc(self, *, year: int | None = None, month: int | None = None) -> int:
        today = _today_ist()
        year = year or today.year
        month = month or today.month
        run = await self._repo.begin_sync(year, month)
        try:
            html = await fetch_necc_sheet_html(self._settings, year=year, month=month)
            parsed = parse_necc_html(html)
            payload = [(row.name, year, month, row.daily, row.sheet_average) for row in parsed]
            count = await self._repo.upsert_zone_rows(payload, year=year, month=month)
            await self._repo.finish_sync(run, status='ok', rows=count, error=None)
            await self._db.commit()
            await self._invalidate_dashboard_cache()
            log.info('necc_sync_ok', year=year, month=month, rows=count)
            return count
        except Exception as exc:
            await self._repo.finish_sync(run, status='error', rows=0, error=str(exc)[:500])
            await self._db.commit()
            log.warning('necc_sync_failed', year=year, month=month, error=str(exc))
            raise

    async def sync_recent_months(self) -> int:
        """Pull the same month/year sheets NECC exposes (ddlMonth/ddlYear), going back N months."""
        total = 0
        anchor = _today_ist()
        for year, month in list_recent_months(anchor, self._settings.market_sync_months_back):
            try:
                total += await self.sync_necc(year=year, month=month)
            except Exception:
                log.warning('necc_sync_month_failed', year=year, month=month)
        return total

    async def list_zones(self, *, include_today: bool = False) -> MarketZonesResponse:
        names = await self._repo.list_zone_names()
        today_map: dict[str, MarketTickerItem] = {}
        if include_today and names:
            try:
                snapshot = await self.compare(names)
                today_map = {item.zone: item for item in snapshot.zones}
            except MarketDataUnavailableError:
                today_map = {}
        zones = [
            MarketZoneItem(
                name=n,
                today_value=today_map[n].value if n in today_map else None,
                today_change=today_map[n].change if n in today_map else None,
            )
            for n in names
        ]
        return MarketZonesResponse(
            zones=zones,
            default_zone=self._settings.market_default_zone,
        )

    async def all_cities_today(self) -> MarketCompareResponse:
        names = await self._repo.list_zone_names()
        if not names:
            raise MarketDataUnavailableError(
                'Egg rates have not been synced yet',
                details=[{'field': 'sync', 'message': 'Run NECC sync first'}],
            )
        return await self.compare(names)

    async def egg_trend(
        self,
        zone_name: str,
        window: str,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> MarketTrendResponse:
        if window not in {'days', 'month', 'year'}:
            from app.core.exceptions import AppError

            raise AppError('window must be days, month, or year', code='validation_error')
        known = set(await self._repo.list_zone_names())
        if not known:
            raise MarketDataUnavailableError('Egg rates have not been synced yet')
        zone_name = validate_zone_or_raise(zone_name, known)
        zone = await self._repo.get_zone_by_name(zone_name)
        assert zone is not None
        today = _today_ist()
        sheet_year = year or today.year
        sheet_month = month or today.month
        month_map = await self._repo.prices_for_zone_month(zone.id, sheet_year, sheet_month)
        summary = await self._repo.month_summary(zone.id, sheet_year, sheet_month)
        sheet_avg = summary.necc_sheet_average if summary else None

        points: list[MarketTrendPoint] = []
        series: list[float]
        if window == 'month':
            if not month_map:
                raise MarketDataUnavailableError(
                    f'No egg prices for {zone_name} in {sheet_year}-{sheet_month:02d}'
                )
            series = [float(month_map[d]) for d in sorted(month_map)]
            for day in sorted(month_map):
                points.append(
                    MarketTrendPoint(
                        date=date(sheet_year, sheet_month, day).isoformat(),
                        value=float(month_map[day]),
                    )
                )
        elif window == 'year':
            year_rows = await self._repo.monthly_averages(zone.id, months=12)
            series = [float(avg) for _, _, avg in year_rows]
            for y, m, avg in year_rows:
                points.append(MarketTrendPoint(date=f'{y}-{m:02d}', value=float(avg)))
        else:
            history_days = 7
            series = []
            for offset in range(history_days - 1, -1, -1):
                d = today - timedelta(days=offset)
                p = await self._repo.price_on_date(zone.id, d)
                if p is not None:
                    val = float(p)
                    series.append(val)
                    points.append(MarketTrendPoint(date=d.isoformat(), value=val))
        if not series:
            raise MarketDataUnavailableError('No trend data for this window')
        return MarketTrendResponse(
            zone=zone_name,
            window=window,
            series=series,
            points=points,
            year=sheet_year if window == 'month' else None,
            month=sheet_month if window == 'month' else None,
            sheet_average=_decimal_str(sheet_avg) if sheet_avg is not None else None,
        )

    async def egg_sheet(self, zone_name: str, year: int, month: int) -> MarketEggSheetResponse:
        known = set(await self._repo.list_zone_names())
        zone_name = validate_zone_or_raise(zone_name, known)
        zone = await self._repo.get_zone_by_name(zone_name)
        assert zone is not None
        month_map = await self._repo.prices_for_zone_month(zone.id, year, month)
        if not month_map:
            raise MarketDataUnavailableError(f'No synced NECC sheet for {zone_name} {year}-{month:02d}')
        summary = await self._repo.month_summary(zone.id, year, month)
        today = _today_ist()
        days: list[MarketDayPrice] = []
        for day in sorted(month_map):
            days.append(
                MarketDayPrice(
                    day=day,
                    date=date(year, month, day).isoformat(),
                    price=_decimal_str(month_map[day]),
                )
            )
        yday, tday, tmrw = None, None, None
        if today.year == year and today.month == month:
            td = today.day
            tday = _decimal_str(month_map[td]) if td in month_map else None
            if td - 1 in month_map:
                yday = _decimal_str(month_map[td - 1])
            if td + 1 in month_map:
                tmrw = _decimal_str(month_map[td + 1])
        return MarketEggSheetResponse(
            zone=zone_name,
            year=year,
            month=month,
            today=tday,
            yesterday=yday,
            tomorrow=tmrw,
            necc_sheet_average=_decimal_str(summary.necc_sheet_average)
            if summary and summary.necc_sheet_average is not None
            else None,
            computed_daily_average=_decimal_str(summary.computed_daily_average)
            if summary and summary.computed_daily_average is not None
            else None,
            days=days,
        )

    async def set_preferred_zone(
        self,
        user_id: uuid.UUID,
        zone: str,
        *,
        session_id: uuid.UUID | None = None,
    ) -> str:
        known = set(await self._repo.list_zone_names())
        validate_zone_or_raise(zone, known)
        user = await self._users.get_self(user_id)
        if user is None or user.profile is None:
            raise MarketDataUnavailableError('Profile required to save zone preference')
        user.profile.market_preferred_zone = zone
        await self._db.commit()
        await self._invalidate_dashboard_cache(user_id=user_id)
        if session_id is not None:
            from app.core.store import warm_session_caches
            from app.core.user_snapshot import permission_codes, profile_snapshot

            fresh = await self._users.get_self(user_id)
            assert fresh is not None
            pwd = fresh.password_changed_at
            if pwd is not None and pwd.tzinfo is None:
                pwd = pwd.replace(tzinfo=UTC)
            epoch = '' if pwd is None else str(int(pwd.timestamp()))
            await warm_session_caches(
                session_id=str(session_id),
                user_id=str(fresh.id),
                role=fresh.role.name,
                is_active=fresh.is_active,
                is_email_verified=fresh.is_email_verified,
                permissions=permission_codes(fresh),
                pwd_epoch=epoch,
                token_version=fresh.token_version,
                profile=profile_snapshot(fresh),
            )
        return zone

    async def dashboard(
        self,
        user: User,
        *,
        zone_query: str | None = None,
    ) -> MarketDashboardResponse:
        cache_key = self._cache_key(user.id, zone_query)
        if self._settings.market_dashboard_cache_seconds > 0:
            cached = await get_redis().get(cache_key)
            if cached:
                return MarketDashboardResponse.model_validate_json(cached)

        names = await self._repo.list_zone_names()
        if not names:
            raise MarketDataUnavailableError(
                'Egg rates have not been synced yet',
                details=[{'field': 'sync', 'message': 'No NECC data in database'}],
            )
        known = set(names)
        profile = user.profile
        if (
            zone_query is None
            and profile
            and not profile.has_location_for_market()
            and not (profile.market_preferred_zone and profile.market_preferred_zone in known)
        ):
            raise ProfileLocationRequiredError(
                details=[
                    {'field': 'pincode', 'message': 'Required'},
                    {'field': 'state', 'message': 'Required'},
                    {'field': 'district', 'message': 'Required'},
                ]
            )
        if zone_query is not None:
            selected = validate_zone_or_raise(zone_query.strip(), known)
            resolved = None
            match_status = 'explicit'
        else:
            preferred = profile.market_preferred_zone if profile else None
            selected, resolved, match_status = resolve_zone_name(
                preferred=preferred,
                village_city=profile.village_city if profile else None,
                district=profile.district if profile else None,
                state=profile.state if profile else None,
                known_zones=known,
                fallback=self._settings.market_default_zone or DEFAULT_FALLBACK_ZONE,
            )
            selected = validate_zone_or_raise(selected, known)

        zone = await self._repo.get_zone_by_name(selected)
        assert zone is not None
        today = _today_ist()
        month_map = await self._repo.prices_for_zone_month(zone.id, today.year, today.month)
        if not month_map:
            raise MarketDataUnavailableError(
                f'No egg prices for {selected} this month',
                details=[{'field': 'zone', 'message': selected}],
            )

        try:
            sheet_day, today_p, is_live_today = spot_from_sheet(month_map, today.day)
        except ValueError:
            raise MarketDataUnavailableError(
                'No egg prices on the current NECC sheet for this zone'
            ) from None
        price_as_of = date(today.year, today.month, sheet_day).isoformat()
        prev_p = neighbor_on_sheet(month_map, sheet_day, -1)
        if prev_p is None and sheet_day == 1:
            yesterday = today - timedelta(days=1)
            prev_p = await self._repo.price_on_date(zone.id, yesterday)
        next_p = neighbor_on_sheet(month_map, sheet_day, 1)

        month_series = [float(month_map[d]) for d in sorted(month_map)]
        year_rows = await self._repo.monthly_averages(zone.id, months=12)
        year_series = [float(avg) for _, _, avg in year_rows] or month_series[-12:]
        history_days = 7
        history: list[float] = []
        for offset in range(history_days - 1, -1, -1):
            d = today - timedelta(days=offset)
            p = await self._repo.price_on_date(zone.id, d)
            if p is not None:
                history.append(float(p))

        change = float(today_p - prev_p) if prev_p is not None else 0.0
        sync = await self._repo.latest_successful_sync()
        fetched_at = sync.finished_at.isoformat() if sync and sync.finished_at else None
        summary = await self._repo.month_summary(zone.id, today.year, today.month)
        necc_avg = summary.necc_sheet_average if summary else None
        comp_avg = summary.computed_daily_average if summary else None

        rate = MarketRateCard(
            value=_decimal_str(today_p),
            previousValue=_decimal_str(prev_p) if prev_p is not None else _decimal_str(today_p),
            nextValue=_decimal_str(next_p) if next_p is not None else _decimal_str(today_p),
            month=month_series,
            year=year_series,
            history=history or month_series[-7:],
            zone=selected,
            month_average=_decimal_str(comp_avg) if comp_avg is not None else None,
            necc_sheet_average=_decimal_str(necc_avg) if necc_avg is not None else None,
        )
        ticker = [
            MarketTickerItem(
                label='Egg rate',
                value=rate.value,
                change=round(change, 2),
                zone=selected,
            )
        ]
        meta = MarketDashboardMetadata(
            selected_zone=selected,
            resolved_zone=resolved,
            match_status=match_status,
            fallback_zone=self._settings.market_default_zone,
            fetched_at=fetched_at,
            zones_version=fetched_at,
            price_as_of=price_as_of,
            is_live_today=is_live_today,
        )
        body = MarketDashboardResponse(metadata=meta, rates=[rate], ticker=ticker)
        if self._settings.market_dashboard_cache_seconds > 0:
            await get_redis().set(
                cache_key,
                body.model_dump_json(),
                ex=self._settings.market_dashboard_cache_seconds,
            )
        return body

    async def compare(self, zones: list[str]) -> MarketCompareResponse:
        known = set(await self._repo.list_zone_names())
        if not known:
            raise MarketDataUnavailableError('No synced NECC data')
        items: list[MarketTickerItem] = []
        today = _today_ist()
        sync = await self._repo.latest_successful_sync()
        limit = self._settings.market_compare_max_zones
        for name in zones[:limit]:
            name = validate_zone_or_raise(name.strip(), known)
            zone = await self._repo.get_zone_by_name(name)
            assert zone
            month_map = await self._repo.prices_for_zone_month(zone.id, today.year, today.month)
            if not month_map:
                continue
            try:
                sheet_day, today_p, _ = spot_from_sheet(month_map, today.day)
            except ValueError:
                continue
            prev_p = neighbor_on_sheet(month_map, sheet_day, -1)
            if prev_p is None and sheet_day == 1:
                prev_p = await self._repo.price_on_date(zone.id, today - timedelta(days=1))
            change = float(today_p - prev_p) if prev_p is not None else 0.0
            items.append(
                MarketTickerItem(
                    label='Egg rate',
                    value=_decimal_str(today_p),
                    change=round(change, 2),
                    zone=name,
                )
            )
        if not items:
            raise MarketDataUnavailableError('No prices for requested zones')
        as_of = sync.finished_at.isoformat() if sync and sync.finished_at else None
        return MarketCompareResponse(zones=items, as_of=as_of)

    def _cache_key(self, user_id: uuid.UUID, zone_query: str | None) -> str:
        suffix = zone_query or 'profile'
        return f'market:dash:{user_id}:{suffix}'

    async def _invalidate_dashboard_cache(self, user_id: uuid.UUID | None = None) -> None:
        if user_id:
            await get_redis().delete(self._cache_key(user_id, None))
            return
        # Best-effort pattern delete skipped: TTL covers full invalidation.
        pass
