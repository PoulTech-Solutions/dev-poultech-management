from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser
from app.core.rate_limit import limit
from app.db.session import get_db
from app.modules.market.schemas import (
    MarketCompareResponse,
    MarketDashboardResponse,
    MarketEggSheetResponse,
    MarketTrendResponse,
    MarketZonePreferenceRequest,
    MarketZonesResponse,
)
from app.modules.market.service import MarketService
from app.modules.users.repository import UsersRepository

router = APIRouter(prefix='/market', tags=['market'])

DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_market_service(db: DbDep, settings: SettingsDep) -> MarketService:
    return MarketService(db, settings)


MarketS = Annotated[MarketService, Depends(get_market_service)]


async def _load_user(db: DbDep, user: CurrentUser):
    repo = UsersRepository(db)
    account = await repo.get_self(user.user_id)
    if account is None:
        from app.core.exceptions import UnauthorizedError

        raise UnauthorizedError('Account no longer exists', code='account_gone')
    return account


@router.get('/zones', dependencies=[Depends(limit('market:read'))])
async def list_zones(
    user: CurrentUser,
    market: MarketS,
    include_today: bool = Query(default=False, description='Attach today NECC price per city when synced'),
) -> MarketZonesResponse:
    return await market.list_zones(include_today=include_today)


@router.get('/dashboard', dependencies=[Depends(limit('market:read'))])
async def market_dashboard(
    user: CurrentUser,
    market: MarketS,
    db: DbDep,
    zone: str | None = Query(default=None, max_length=120),
) -> MarketDashboardResponse:
    account = await _load_user(db, user)
    return await market.dashboard(account, zone_query=zone)


@router.patch('/zone-preference', dependencies=[Depends(limit('market:write'))])
async def set_zone_preference(
    payload: MarketZonePreferenceRequest,
    user: CurrentUser,
    market: MarketS,
) -> dict:
    zone = await market.set_preferred_zone(
        user.user_id, payload.zone, session_id=user.session_id
    )
    return {'market_preferred_zone': zone}


@router.get('/eggs/cities-today', dependencies=[Depends(limit('market:read'))])
async def all_cities_today(user: CurrentUser, market: MarketS) -> MarketCompareResponse:
    """Today's egg rate for every NECC city stored in the database (after sync)."""
    return await market.all_cities_today()


@router.get('/eggs/trend', dependencies=[Depends(limit('market:read'))])
async def egg_trend(
    user: CurrentUser,
    market: MarketS,
    zone: str = Query(..., max_length=120),
    window: str = Query(default='days', pattern='^(days|month|year)$'),
    year: int | None = Query(default=None, ge=2016, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
) -> MarketTrendResponse:
    return await market.egg_trend(zone, window, year=year, month=month)


@router.get('/eggs/sheet', dependencies=[Depends(limit('market:read'))])
async def egg_sheet(
    user: CurrentUser,
    market: MarketS,
    zone: str = Query(..., max_length=120),
    year: int = Query(..., ge=2016, le=2100),
    month: int = Query(..., ge=1, le=12),
) -> MarketEggSheetResponse:
    return await market.egg_sheet(zone, year, month)


@router.get('/eggs/compare', dependencies=[Depends(limit('market:read'))])
async def compare_eggs(
    user: CurrentUser,
    market: MarketS,
    zones: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description='Comma-separated NECC zone names',
    ),
) -> MarketCompareResponse:
    names = [part.strip() for part in zones.split(',') if part.strip()]
    return await market.compare(names)
