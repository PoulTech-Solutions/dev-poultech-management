from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.constants import RoleName
from app.core.exceptions import ConflictError, ForbiddenError
from app.core.market_errors import MarketDataUnavailableError
from app.core.redis_conn import get_redis
from app.db.models.users import User
from app.modules.market.calendar_util import list_recent_months, week_day_span, week_of_month
from app.modules.market.necc_client import fetch_necc_sheet_html, parse_necc_html
from app.modules.market.pricing_util import latest_day_on_sheet, neighbor_on_sheet, spot_from_sheet
from app.modules.market.products import MANUAL_PRODUCTS, POULTRY_PRODUCTS
from app.modules.market.repository import MarketRepository
from app.modules.market.schemas import (
    ManualPoultryRateReadResponse,
    ManualPoultryRateUpsertResponse,
    MarketCompareResponse,
    MarketCoverageResponse,
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
    state_label_for_zone,
    validate_zone_or_raise,
)
from app.modules.users.repository import UsersRepository

log = structlog.get_logger()

_REDIS_OP_TIMEOUT = 2.0


async def _redis_get(key: str) -> str | None:
    try:
        return await asyncio.wait_for(get_redis().get(key), timeout=_REDIS_OP_TIMEOUT)
    except (TimeoutError, OSError, ConnectionError) as exc:
        log.warning('market_redis_get_skipped', key=key, error=str(exc))
        return None


async def _redis_set(key: str, value: str, *, ex: int) -> None:
    try:
        await asyncio.wait_for(get_redis().set(key, value, ex=ex), timeout=_REDIS_OP_TIMEOUT)
    except (TimeoutError, OSError, ConnectionError) as exc:
        log.warning('market_redis_set_skipped', key=key, error=str(exc))


async def _redis_incr(key: str) -> None:
    try:
        await asyncio.wait_for(get_redis().incr(key), timeout=_REDIS_OP_TIMEOUT)
    except (TimeoutError, OSError, ConnectionError) as exc:
        log.warning('market_redis_incr_skipped', key=key, error=str(exc))


async def _redis_delete(key: str) -> None:
    try:
        await asyncio.wait_for(get_redis().delete(key), timeout=_REDIS_OP_TIMEOUT)
    except (TimeoutError, OSError, ConnectionError) as exc:
        log.warning('market_redis_delete_skipped', key=key, error=str(exc))


def _decimal_str(value: Decimal | float) -> str:
    if isinstance(value, Decimal):
        rounded = value.quantize(Decimal('0.01'))
        return str(int(rounded)) if rounded == rounded.to_integral_value() else str(rounded)
    return str(round(float(value), 2))


def _optional_price_str(value: Decimal | None) -> str:
    """Empty string when NECC has no column yet (frontend shows N/A)."""
    return _decimal_str(value) if value is not None else ''


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
                state=state_label_for_zone(n),
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

    async def egg_coverage(self, zone_name: str) -> MarketCoverageResponse:
        known = set(await self._repo.list_zone_names())
        if not known:
            raise MarketDataUnavailableError('Egg rates have not been synced yet')
        zone_name = validate_zone_or_raise(zone_name, known)
        zone = await self._repo.get_zone_by_name(zone_name)
        assert zone is not None
        today = _today_ist()
        since = date(today.year - 4, 1, 1)
        grouped: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
        for year, month, day in await self._repo.published_dates_for_zone(zone.id, since=since):
            grouped[year][month].add(day)
        years = sorted(grouped, reverse=True)[:5]
        months = {str(year): sorted(grouped[year]) for year in years}
        weeks = {
            f'{year}-{month:02d}': sorted({week_of_month(day) for day in days})
            for year in years
            for month, days in grouped[year].items()
        }
        return MarketCoverageResponse(zone=zone_name, years=years, months=months, weeks=weeks)

    async def egg_trend(
        self,
        zone_name: str,
        window: str,
        *,
        year: int | None = None,
        month: int | None = None,
        week: int | None = None,
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
        trend_week: int | None = None
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
            year_rows = await self._repo.monthly_averages_for_year(zone.id, sheet_year)
            series = [float(avg) for _, _, avg in year_rows]
            for y, m, avg in year_rows:
                points.append(MarketTrendPoint(date=f'{y}-{m:02d}', value=float(avg)))
        else:
            if not month_map:
                raise MarketDataUnavailableError(
                    f'No egg prices for {zone_name} in {sheet_year}-{sheet_month:02d}'
                )
            if week is not None:
                from app.core.exceptions import AppError

                try:
                    start, end = week_day_span(sheet_year, sheet_month, week)
                except ValueError as exc:
                    raise AppError(str(exc), code='validation_error') from exc
                trend_week = week
            else:
                start, end = 1, 31
            days = [d for d in sorted(month_map) if start <= d <= end]
            if week is None:
                days = days[-7:]
            series = []
            for day in days:
                val = float(month_map[day])
                series.append(val)
                points.append(
                    MarketTrendPoint(
                        date=date(sheet_year, sheet_month, day).isoformat(),
                        value=val,
                    )
                )
        if not series:
            raise MarketDataUnavailableError('No trend data for this window')
        return MarketTrendResponse(
            zone=zone_name,
            window=window,
            series=series,
            points=points,
            year=sheet_year,
            month=sheet_month if window in {'days', 'month'} else None,
            week=trend_week,
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

    async def upsert_manual_poultry_rates(
        self,
        *,
        product: str,
        zone_name: str,
        today: Decimal | None,
        yesterday: Decimal | None,
        tomorrow: Decimal | None,
        updated_by: uuid.UUID,
        actor_role: str,
    ) -> ManualPoultryRateUpsertResponse:
        known = set(await self._repo.list_zone_names())
        if not known:
            raise MarketDataUnavailableError('No NECC zones in database')
        zone_name = validate_zone_or_raise(zone_name.strip(), known)
        zone = await self._repo.get_zone_by_name(zone_name)
        assert zone is not None
        today_d = _today_ist()
        await self._repo.lock_poultry_channel(zone.id, product)
        writes: list[tuple[date, Decimal]] = []
        if today is not None:
            writes.append((today_d, today))
        if yesterday is not None:
            writes.append((today_d - timedelta(days=1), yesterday))
        if tomorrow is not None:
            writes.append((today_d + timedelta(days=1), tomorrow))
        for rate_date, price in writes:
            await self._assert_poultry_writable(
                zone_id=zone.id,
                product=product,
                rate_date=rate_date,
                today_d=today_d,
                actor_role=actor_role,
            )
            await self._repo.upsert_poultry_daily(
                zone_id=zone.id,
                product=product,
                rate_date=rate_date,
                price=price,
                source='manual',
                updated_by=updated_by,
            )
        try:
            await self._db.commit()
        except IntegrityError:
            await self._db.rollback()
            raise ConflictError(
                'A rate for this city, product, and day already exists',
                code='rate_conflict',
            ) from None
        await self._bump_rates_epoch()
        read = await self.read_manual_poultry_rates(
            product=product, zone_name=zone_name, actor_role=actor_role
        )
        return ManualPoultryRateUpsertResponse(
            product=read.product,
            zone=read.zone,
            source=read.source,
            today=read.today or (_decimal_str(today) if today is not None else ''),
            yesterday=read.yesterday,
            tomorrow=read.tomorrow,
            today_source_date=read.today_source_date,
            yesterday_source_date=read.yesterday_source_date,
            today_carried=read.today_carried,
            yesterday_carried=read.yesterday_carried,
            today_editable=read.today_editable,
            yesterday_editable=read.yesterday_editable,
            tomorrow_editable=read.tomorrow_editable,
        )

    async def _assert_poultry_writable(
        self,
        *,
        zone_id: uuid.UUID,
        product: str,
        rate_date: date,
        today_d: date,
        actor_role: str,
    ) -> None:
        if actor_role in {RoleName.ADMIN, RoleName.SUPER_ADMIN}:
            return
        if rate_date > today_d:
            return
        existing = await self._repo.poultry_price_on_date(zone_id, product, rate_date)
        if existing is not None:
            raise ForbiddenError(
                'Staff cannot change yesterday or today once that day has a rate. Ask an admin.',
                code='rate_locked',
            )

    async def read_manual_poultry_rates(
        self, *, product: str, zone_name: str, actor_role: str = 'staff'
    ) -> ManualPoultryRateReadResponse:
        known = set(await self._repo.list_zone_names())
        if not known:
            raise MarketDataUnavailableError('No NECC zones in database')
        zone_name = validate_zone_or_raise(zone_name.strip(), known)
        zone = await self._repo.get_zone_by_name(zone_name)
        assert zone is not None
        today_d = _today_ist()
        today_row = await self._repo.latest_poultry_on_or_before(zone.id, product, today_d)
        yday = today_d - timedelta(days=1)
        y_row = await self._repo.latest_poultry_on_or_before(zone.id, product, yday)
        next_p = await self._repo.poultry_price_on_date(zone.id, product, today_d + timedelta(days=1))
        today_exact = today_row is not None and today_row[0] == today_d
        y_exact = y_row is not None and y_row[0] == yday
        can_override = actor_role in {RoleName.ADMIN, RoleName.SUPER_ADMIN}
        return ManualPoultryRateReadResponse(
            product=product,
            zone=zone_name,
            source='manual',
            today=_decimal_str(today_row[1]) if today_row else None,
            yesterday=_decimal_str(y_row[1]) if y_row else None,
            tomorrow=_decimal_str(next_p) if next_p is not None else None,
            today_source_date=today_row[0].isoformat() if today_row else None,
            yesterday_source_date=y_row[0].isoformat() if y_row else None,
            today_carried=bool(today_row) and not today_exact,
            yesterday_carried=bool(y_row) and not y_exact,
            today_editable=can_override or not today_exact,
            yesterday_editable=can_override or not y_exact,
            tomorrow_editable=True,
        )

    async def dashboard(
        self,
        user: User,
        *,
        zone_query: str | None = None,
    ) -> MarketDashboardResponse:
        cache_key = await self._dashboard_cache_key(user.id, zone_query, user.profile)
        if self._settings.market_dashboard_cache_seconds > 0:
            cached = await _redis_get(cache_key)
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
        if zone_query is not None:
            selected = validate_zone_or_raise(zone_query.strip(), known)
            resolved = None
            match_status = 'explicit'
        else:
            preferred = profile.market_preferred_zone if profile else None
            selected, resolved, match_status = resolve_zone_name(
                preferred=preferred,
                village_city=profile.village_city if profile else None,
                tehsil=profile.tehsil if profile else None,
                district=profile.district if profile else None,
                state=profile.state if profile else None,
                pincode=profile.pincode if profile else None,
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

        today_p = month_map.get(today.day)
        is_live_today = today_p is not None
        latest = latest_day_on_sheet(month_map, through_day=today.day)
        if today_p is None and latest is None:
            raise MarketDataUnavailableError(
                'No egg prices on the current NECC sheet for this zone'
            )
        price_as_of = date(today.year, today.month, today.day if is_live_today else latest).isoformat()
        prev_p = month_map.get(today.day - 1)
        if prev_p is None and today.day == 1:
            prev_p = await self._repo.price_on_date(zone.id, today - timedelta(days=1))
        next_p = month_map.get(today.day + 1)

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

        change = (
            float(today_p - prev_p) if today_p is not None and prev_p is not None else 0.0
        )
        sync = await self._repo.latest_successful_sync()
        fetched_at = sync.finished_at.isoformat() if sync and sync.finished_at else None
        summary = await self._repo.month_summary(zone.id, today.year, today.month)
        necc_avg = summary.necc_sheet_average if summary else None
        comp_avg = summary.computed_daily_average if summary else None

        rate = MarketRateCard(
            id='eggs',
            title='Eggs',
            unit='per 100 eggs (NECC suggested)',
            source='necc',
            live=True,
            value=_optional_price_str(today_p),
            previousValue=_optional_price_str(prev_p),
            nextValue=_optional_price_str(next_p),
            month=month_series,
            year=year_series,
            history=history or month_series[-7:],
            zone=selected,
            month_average=_decimal_str(comp_avg) if comp_avg is not None else None,
            necc_sheet_average=_decimal_str(necc_avg) if necc_avg is not None else None,
        )
        poultry_cards: list[MarketRateCard] = []
        ticker = [
            MarketTickerItem(
                label='Egg rate',
                value=rate.value,
                change=round(change, 2),
                zone=selected,
            )
        ]
        for product in sorted(MANUAL_PRODUCTS):
            card = await self._poultry_rate_card(zone.id, product, selected, today)
            if card is None:
                continue
            poultry_cards.append(card)
            today_for_change = Decimal(card.value)
            prev_for_change = Decimal(card.previousValue) if card.previousValue else today_for_change
            ticker.append(
                MarketTickerItem(
                    label=POULTRY_PRODUCTS[product]['ticker_label'],
                    value=card.value,
                    change=round(float(today_for_change - prev_for_change), 2),
                    zone=selected,
                )
            )
        rates = [rate, *poultry_cards]
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
        body = MarketDashboardResponse(metadata=meta, rates=rates, ticker=ticker)
        if self._settings.market_dashboard_cache_seconds > 0:
            await _redis_set(
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

    def _cache_key(
        self,
        user_id: uuid.UUID,
        zone_query: str | None,
        profile: object | None = None,
    ) -> str:
        suffix = zone_query or 'profile'
        pin = getattr(profile, 'pincode', None) or ''
        district = getattr(profile, 'district', None) or ''
        preferred = getattr(profile, 'market_preferred_zone', None) or ''
        return f'market:dash:{user_id}:{suffix}:{pin}:{district}:{preferred}'

    async def _dashboard_cache_key(
        self,
        user_id: uuid.UUID,
        zone_query: str | None,
        profile: object | None = None,
    ) -> str:
        epoch = await self._rates_epoch()
        return f'{self._cache_key(user_id, zone_query, profile)}:p{epoch}'

    async def _rates_epoch(self) -> int:
        raw = await _redis_get('market:poultry:epoch')
        return int(raw) if raw else 0

    async def _bump_rates_epoch(self) -> None:
        await _redis_incr('market:poultry:epoch')

    async def _poultry_rate_card(
        self,
        zone_id: uuid.UUID,
        product: str,
        zone_name: str,
        today: date,
    ) -> MarketRateCard | None:
        meta = POULTRY_PRODUCTS[product]
        since = today - timedelta(days=370)
        daily_rows = await self._repo.poultry_prices_since(zone_id, product, since)
        if not daily_rows:
            return None

        def carried(target: date) -> Decimal | None:
            last: Decimal | None = None
            for published, price in daily_rows:
                if published > target:
                    break
                last = price
            return last

        today_p = carried(today)
        if today_p is None:
            return None
        prev_p = carried(today - timedelta(days=1))
        by_date = dict(daily_rows)
        next_p = by_date.get(today + timedelta(days=1))
        month_map = await self._repo.poultry_prices_for_month(zone_id, product, today.year, today.month)
        month_series = [float(month_map[d]) for d in sorted(month_map)] if month_map else [float(today_p)]
        year_series = _poultry_month_averages(daily_rows, today) or month_series[-12:]
        history: list[float] = []
        for offset in range(6, -1, -1):
            p = carried(today - timedelta(days=offset))
            if p is not None:
                history.append(float(p))
        if not history:
            history = month_series[-7:] if len(month_series) >= 7 else month_series

        return MarketRateCard(
            id=meta['id'],
            title=meta['title'],
            unit=meta['unit'],
            source='manual',
            live=True,
            value=_decimal_str(today_p),
            previousValue=_optional_price_str(prev_p),
            nextValue=_optional_price_str(next_p),
            month=month_series,
            year=year_series,
            history=history,
            zone=zone_name,
        )

    async def _invalidate_dashboard_cache(self, user_id: uuid.UUID | None = None) -> None:
        if user_id:
            await _redis_delete(await self._dashboard_cache_key(user_id, None))
            return
        try:
            redis = get_redis()
            async for key in redis.scan_iter(match='market:dash:*', count=100):
                await asyncio.wait_for(redis.delete(key), timeout=_REDIS_OP_TIMEOUT)
        except (TimeoutError, OSError, ConnectionError) as exc:
            log.warning('market_redis_scan_delete_skipped', error=str(exc))


def _poultry_month_averages(rows: list[tuple[date, Decimal]], anchor: date, count: int = 12) -> list[float]:
    buckets: dict[tuple[int, int], list[Decimal]] = defaultdict(list)
    for d, price in rows:
        buckets[(d.year, d.month)].append(price)
    series: list[float] = []
    for year, month in reversed(list_recent_months(anchor, count)):
        vals = buckets.get((year, month))
        if vals:
            series.append(float(sum(vals) / len(vals)))
        elif series:
            series.append(series[-1])
    return series
