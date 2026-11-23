from __future__ import annotations

from sqlalchemy import func, or_, select, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.geo import GeoOffice, GeoPincode


class GeoRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, pincode: str) -> GeoPincode | None:
        return await self._db.get(GeoPincode, pincode)

    async def search_prefix(self, prefix: str, *, limit: int) -> list[GeoPincode]:
        stmt = (
            select(GeoPincode)
            .where(GeoPincode.pincode.startswith(prefix))
            .order_by(GeoPincode.pincode)
            .limit(limit)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def search_place(self, term: str, *, limit: int) -> list[GeoPincode]:
        pattern = f'%{term}%'
        pin_stmt = (
            select(GeoPincode)
            .where(
                or_(
                    GeoPincode.state.ilike(pattern),
                    GeoPincode.district.ilike(pattern),
                    GeoPincode.tehsil.ilike(pattern),
                )
            )
            .order_by(GeoPincode.state, GeoPincode.district, GeoPincode.pincode)
            .limit(limit)
        )
        pins = list((await self._db.execute(pin_stmt)).scalars().all())
        if len(pins) >= limit:
            return pins
        office_stmt = (
            select(GeoPincode)
            .join(GeoOffice, GeoOffice.pincode == GeoPincode.pincode)
            .where(GeoOffice.name.ilike(pattern))
            .order_by(GeoPincode.pincode)
            .limit(limit - len(pins))
        )
        extra = list((await self._db.execute(office_stmt)).scalars().all())
        seen = {row.pincode for row in pins}
        for row in extra:
            if row.pincode not in seen:
                pins.append(row)
                seen.add(row.pincode)
        return pins

    async def list_states(self) -> list[str]:
        stmt = select(GeoPincode.state).distinct().order_by(GeoPincode.state)
        return [row[0] for row in (await self._db.execute(stmt)).all()]

    async def list_districts(self, state: str) -> list[str]:
        stmt = (
            select(GeoPincode.district)
            .where(GeoPincode.state == state.strip())
            .distinct()
            .order_by(GeoPincode.district)
        )
        return [row[0] for row in (await self._db.execute(stmt)).all()]

    async def list_tehsils(self, *, state: str, district: str) -> list[str]:
        st = state.strip()
        dist = district.strip()
        from_pins = (
            select(GeoPincode.tehsil.label('name'))
            .where(
                GeoPincode.state == st,
                GeoPincode.district == dist,
                GeoPincode.tehsil.is_not(None),
                GeoPincode.tehsil != '',
            )
        )
        from_offices = (
            select(GeoOffice.taluk.label('name'))
            .join(GeoPincode, GeoOffice.pincode == GeoPincode.pincode)
            .where(
                GeoPincode.state == st,
                GeoPincode.district == dist,
                GeoOffice.taluk.is_not(None),
                GeoOffice.taluk != '',
            )
        )
        combined = union(from_pins, from_offices).subquery()
        stmt = select(combined.c.name).where(combined.c.name.is_not(None)).order_by(combined.c.name)
        return [row[0] for row in (await self._db.execute(stmt)).all() if row[0]]

    async def list_offices(
        self,
        *,
        state: str,
        district: str,
        tehsil: str | None,
        limit: int,
        q: str | None = None,
    ) -> list[tuple[GeoOffice, GeoPincode]]:
        filters = [
            GeoPincode.state == state.strip(),
            GeoPincode.district == district.strip(),
        ]
        if tehsil:
            area = tehsil.strip()
            filters.append(or_(GeoOffice.taluk == area, GeoPincode.tehsil == area))
        if q:
            filters.append(GeoOffice.name.ilike(f'%{q}%'))
        stmt = (
            select(GeoOffice, GeoPincode)
            .join(GeoPincode, GeoOffice.pincode == GeoPincode.pincode)
            .where(*filters)
            .order_by(GeoOffice.name)
            .limit(limit)
        )
        return list((await self._db.execute(stmt)).all())

    async def list_pincodes(
        self, *, state: str, district: str, tehsil: str | None, limit: int
    ) -> list[GeoPincode]:
        filters = [
            GeoPincode.state == state.strip(),
            GeoPincode.district == district.strip(),
        ]
        if tehsil:
            area = tehsil.strip()
            office_pins = select(GeoOffice.pincode).where(GeoOffice.taluk == area)
            filters.append(or_(GeoPincode.tehsil == area, GeoPincode.pincode.in_(office_pins)))
        stmt = select(GeoPincode).where(*filters).order_by(GeoPincode.pincode).limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def upsert_offices(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        stmt = insert(GeoOffice).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint='uq_geo_offices_pincode_name',
            set_={'office_type': stmt.excluded.office_type, 'taluk': stmt.excluded.taluk},
        )
        await self._db.execute(stmt)
        await self._db.flush()
        return len(rows)

    async def upsert_rows(self, rows: list[dict[str, str | None]]) -> int:
        if not rows:
            return 0
        stmt = insert(GeoPincode).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[GeoPincode.pincode],
            set_={
                'state': stmt.excluded.state,
                'district': stmt.excluded.district,
                'tehsil': stmt.excluded.tehsil,
            },
        )
        await self._db.execute(stmt)
        await self._db.flush()
        return len(rows)

    async def count(self) -> int:
        return int((await self._db.execute(select(func.count()).select_from(GeoPincode))).scalar_one())
