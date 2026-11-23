from __future__ import annotations

import asyncio
import re

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.geo_errors import InvalidPincodeError
from app.core.redis_conn import get_redis
from app.db.models.geo import GeoPincode
from app.modules.geo.repository import GeoRepository
from app.modules.geo.schemas import (
    GeoNameItem,
    GeoNameListResponse,
    GeoOfficeItem,
    GeoOfficeListResponse,
    GeoPincodeItem,
    GeoPincodeSearchResponse,
)

log = structlog.get_logger()
_PREFIX_RE = re.compile(r'^[1-9][0-9]{0,5}$')
_PLACE_RE = re.compile(r'^[A-Za-z][A-Za-z .\'-]{1,79}$')
_CACHE_TTL = 86_400
_REDIS_OP_TIMEOUT = 1.5
_CATALOG_PREFIX = 'geo:v2:catalog'


def _label(pincode: str, district: str, state: str) -> str:
    return f'{pincode} · {district}, {state}'


class GeoService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._repo = GeoRepository(db)

    def _items(self, rows: list[GeoPincode]) -> list[GeoPincodeItem]:
        return [
            GeoPincodeItem(
                pincode=r.pincode,
                state=r.state,
                district=r.district,
                tehsil=r.tehsil,
                label=_label(r.pincode, r.district, r.state),
            )
            for r in rows
        ]

    async def search(self, query: str, *, limit: int | None = None) -> GeoPincodeSearchResponse:
        q = query.strip()
        cap = min(limit or self._settings.geo_pincode_search_limit, 30)
        if _PREFIX_RE.fullmatch(q):
            if len(q) < self._settings.geo_pincode_search_min_digits:
                raise InvalidPincodeError(
                    f'Type at least {self._settings.geo_pincode_search_min_digits} digits',
                    details=[{'field': 'q', 'message': 'Query too short'}],
                )
            rows = await self._repo.search_prefix(q, limit=cap)
            return GeoPincodeSearchResponse(items=self._items(rows), query=q)
        if _PLACE_RE.fullmatch(q):
            term = re.sub(r'[%_\\]', '', q)
            rows = await self._repo.search_place(term, limit=cap)
            return GeoPincodeSearchResponse(items=self._items(rows), query=q)
        raise InvalidPincodeError(
            'Search by pincode digits or a city / district name',
            details=[{'field': 'q', 'message': 'Invalid search query'}],
        )

    async def list_states(self) -> GeoNameListResponse:
        cached = await self._read_list_cache(f'{_CATALOG_PREFIX}:states', GeoNameListResponse)
        if cached is not None:
            return cached
        body = GeoNameListResponse(items=[GeoNameItem(name=name) for name in await self._repo.list_states()])
        await self._write_list_cache(f'{_CATALOG_PREFIX}:states', body)
        return body

    async def list_districts(self, state: str) -> GeoNameListResponse:
        name = state.strip()
        if not name:
            return GeoNameListResponse(items=[])
        key = f'{_CATALOG_PREFIX}:districts:{name.lower()}'
        cached = await self._read_list_cache(key, GeoNameListResponse)
        if cached is not None:
            return cached
        body = GeoNameListResponse(
            items=[GeoNameItem(name=row) for row in await self._repo.list_districts(name)]
        )
        await self._write_list_cache(key, body)
        return body

    async def list_tehsils(self, state: str, district: str) -> GeoNameListResponse:
        st = state.strip()
        dist = district.strip()
        key = f'{_CATALOG_PREFIX}:tehsils:{st.lower()}:{dist.lower()}'
        cached = await self._read_list_cache(key, GeoNameListResponse)
        if cached is not None:
            return cached
        rows = await self._repo.list_tehsils(state=st, district=dist)
        body = GeoNameListResponse(items=[GeoNameItem(name=name) for name in rows])
        await self._write_list_cache(key, body)
        return body

    async def list_offices(
        self,
        *,
        state: str,
        district: str,
        tehsil: str | None = None,
        limit: int | None = None,
        q: str | None = None,
    ) -> GeoOfficeListResponse:
        cap = min(limit or 50, 100)
        needle = re.sub(r'[%_\\]', '', (q or '').strip()) or None
        rows = await self._repo.list_offices(
            state=state.strip(),
            district=district.strip(),
            tehsil=tehsil,
            limit=cap,
            q=needle,
        )
        items = [
            GeoOfficeItem(
                name=office.name,
                pincode=pin.pincode,
                taluk=office.taluk or pin.tehsil,
                office_type=office.office_type,
                label=f'{office.name} · {pin.pincode}',
            )
            for office, pin in rows
        ]
        return GeoOfficeListResponse(items=items)

    async def list_pincodes(
        self,
        *,
        state: str,
        district: str,
        tehsil: str | None = None,
        limit: int | None = None,
    ) -> GeoPincodeSearchResponse:
        cap = min(limit or 80, 100)
        area = tehsil.strip() if tehsil else None
        rows = await self._repo.list_pincodes(
            state=state.strip(),
            district=district.strip(),
            tehsil=area,
            limit=cap,
        )
        query = f'{state.strip()} / {district.strip()}'
        if area:
            query = f'{query} / {area}'
        return GeoPincodeSearchResponse(items=self._items(rows), query=query)

    async def resolve(self, pincode: str) -> GeoPincodeItem | None:
        cached = await self._read_cache(pincode)
        if cached is not None:
            return cached
        row = await self._repo.get(pincode)
        if row is None:
            return None
        item = GeoPincodeItem(
            pincode=row.pincode,
            state=row.state,
            district=row.district,
            tehsil=row.tehsil,
            label=_label(row.pincode, row.district, row.state),
        )
        await self._write_cache(pincode, item)
        return item

    async def apply_pincode_to_profile_values(self, values: dict) -> dict:
        """If pincode is present, ensure it exists in geo_pincodes and align state/district."""
        pincode = values.get('pincode')
        if not pincode:
            return values
        if not self._settings.geo_pincode_strict:
            return values
        count = await self._repo.count()
        if count == 0:
            log.warning('geo_pincode_table_empty', action='skip_strict_validation')
            return values
        area = await self.resolve(str(pincode))
        if area is None:
            if values.get('state') and values.get('district'):
                log.info('geo_pincode_unknown_accepted', pincode=pincode)
                return values
            raise InvalidPincodeError(
                details=[{'field': 'pincode', 'message': 'Unknown pincode'}],
            )
        values['state'] = area.state
        values['district'] = area.district
        if area.tehsil:
            values['tehsil'] = area.tehsil
        return values

    async def _read_cache(self, pincode: str) -> GeoPincodeItem | None:
        raw = await self._redis_get(f'geo:pin:{pincode}')
        if not raw:
            return None
        try:
            return GeoPincodeItem.model_validate_json(raw)
        except Exception:
            return None

    async def _write_cache(self, pincode: str, item: GeoPincodeItem) -> None:
        await self._redis_set(f'geo:pin:{pincode}', item.model_dump_json())

    async def _read_list_cache(
        self, key: str, model: type[GeoNameListResponse]
    ) -> GeoNameListResponse | None:
        raw = await self._redis_get(key)
        if not raw:
            return None
        try:
            return model.model_validate_json(raw)
        except Exception:
            return None

    async def _write_list_cache(self, key: str, body: GeoNameListResponse) -> None:
        await self._redis_set(key, body.model_dump_json())

    async def _redis_get(self, key: str) -> str | None:
        if self._settings.geo_pincode_cache_seconds <= 0:
            return None
        try:
            return await asyncio.wait_for(get_redis().get(key), timeout=_REDIS_OP_TIMEOUT)
        except Exception:
            return None

    async def _redis_set(self, key: str, value: str) -> None:
        if self._settings.geo_pincode_cache_seconds <= 0:
            return
        try:
            await asyncio.wait_for(
                get_redis().set(key, value, ex=min(self._settings.geo_pincode_cache_seconds, _CACHE_TTL)),
                timeout=_REDIS_OP_TIMEOUT,
            )
        except Exception:
            return
