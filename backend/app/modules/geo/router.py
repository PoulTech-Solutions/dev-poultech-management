from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.rate_limit import limit
from app.db.session import get_db
from app.modules.geo.schemas import (
    GeoNameListResponse,
    GeoOfficeListResponse,
    GeoPincodeItem,
    GeoPincodeSearchResponse,
)
from app.modules.geo.service import GeoService

router = APIRouter(prefix='/geo', tags=['geo'])

DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_geo_service(db: DbDep, settings: SettingsDep) -> GeoService:
    return GeoService(db, settings)


GeoS = Annotated[GeoService, Depends(get_geo_service)]


@router.get('/states', dependencies=[Depends(limit('geo:read'))])
async def list_states(geo: GeoS) -> GeoNameListResponse:
    return await geo.list_states()


@router.get('/districts', dependencies=[Depends(limit('geo:read'))])
async def list_districts(
    geo: GeoS,
    state: str = Query(..., min_length=2, max_length=100),
) -> GeoNameListResponse:
    return await geo.list_districts(state)


@router.get('/tehsils', dependencies=[Depends(limit('geo:read'))])
async def list_tehsils(
    geo: GeoS,
    state: str = Query(..., min_length=2, max_length=100),
    district: str = Query(..., min_length=2, max_length=100),
) -> GeoNameListResponse:
    return await geo.list_tehsils(state, district)


@router.get('/offices', dependencies=[Depends(limit('geo:read'))])
async def list_offices(
    geo: GeoS,
    state: str = Query(..., min_length=2, max_length=100),
    district: str = Query(..., min_length=2, max_length=100),
    tehsil: str | None = Query(default=None, max_length=100),
    q: str | None = Query(default=None, max_length=80, description='Village / post office name'),
    limit: int | None = Query(default=None, ge=1, le=100),
) -> GeoOfficeListResponse:
    return await geo.list_offices(state=state, district=district, tehsil=tehsil, limit=limit, q=q)


@router.get('/pincodes', dependencies=[Depends(limit('geo:read'))])
async def list_pincodes(
    geo: GeoS,
    state: str = Query(..., min_length=2, max_length=100),
    district: str = Query(..., min_length=2, max_length=100),
    tehsil: str | None = Query(default=None, max_length=100),
    limit: int | None = Query(default=None, ge=1, le=100),
) -> GeoPincodeSearchResponse:
    return await geo.list_pincodes(state=state, district=district, tehsil=tehsil, limit=limit)


@router.get('/pincodes/search', dependencies=[Depends(limit('geo:read'))])
async def search_pincodes(
    geo: GeoS,
    q: str = Query(..., min_length=1, max_length=80, description='Pincode prefix or city / district name'),
    limit: int | None = Query(default=None, ge=1, le=30),
) -> GeoPincodeSearchResponse:
    return await geo.search(q, limit=limit)


@router.get('/pincodes/{pincode}', dependencies=[Depends(limit('geo:read'))])
async def get_pincode(geo: GeoS, pincode: str) -> GeoPincodeItem:
    from app.core.geo_errors import InvalidPincodeError

    area = await geo.resolve(pincode)
    if area is None:
        raise InvalidPincodeError(details=[{'field': 'pincode', 'message': 'Unknown pincode'}])
    return area
