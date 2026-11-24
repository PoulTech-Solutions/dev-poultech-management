from __future__ import annotations

import uuid
from datetime import date

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.db.models.mitra import MitraDailyRecord, MitraFarmUnit, MitraHatcheryBatch
from app.modules.mitra.billing_service import MitraBillingMixin
from app.modules.mitra.constants import (
    DAILY_RECORD_TYPES,
    MAX_FARM_UNITS_PER_OWNER,
    FarmUnitType,
)
from app.modules.mitra.export_cache import drop_export_cache
from app.modules.mitra.gst_service import MitraGstMixin
from app.modules.mitra.record_schemas import validate_daily_data, validate_hatchery_batch
from app.modules.mitra.repository import MitraRepository
from app.modules.mitra.schemas import (
    DailyRecordListResponse,
    DailyRecordResponse,
    DailyRecordUpsertRequest,
    FarmUnitCreateRequest,
    FarmUnitListResponse,
    FarmUnitResponse,
    FarmUnitUpdateRequest,
    HatcheryBatchListResponse,
    HatcheryBatchResponse,
    HatcheryBatchUpsertRequest,
)

log = structlog.get_logger()


def _record_validation_error(exc: ValidationError, *, hatchery: bool = False) -> AppError:
    msg = 'Invalid hatchery batch fields' if hatchery else 'Invalid record fields for this farm type'
    return AppError(msg, code='mitra_record_invalid', details=exc.errors())


class MitraService(MitraBillingMixin, MitraGstMixin):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = MitraRepository(db)

    async def list_units(self, owner_user_id: uuid.UUID) -> FarmUnitListResponse:
        units = await self._repo.list_units(owner_user_id)
        return FarmUnitListResponse(
            items=[FarmUnitResponse.model_validate(u) for u in units],
            total=len(units),
            max_units=MAX_FARM_UNITS_PER_OWNER,
        )

    async def create_unit(self, owner_user_id: uuid.UUID, payload: FarmUnitCreateRequest) -> FarmUnitResponse:
        await self._repo.lock_owner_unit_cap(owner_user_id)
        count = await self._repo.count_active_units(owner_user_id)
        if count >= MAX_FARM_UNITS_PER_OWNER:
            raise ConflictError(
                f'You can register at most {MAX_FARM_UNITS_PER_OWNER} farms or sheds',
                code='mitra_unit_cap',
            )
        unit = MitraFarmUnit(
            owner_user_id=owner_user_id,
            name=payload.name,
            farm_type=payload.farm_type.value,
            contact_phone=payload.contact_phone,
            state=payload.state,
            district=payload.district,
            tehsil=payload.tehsil,
            village_city=payload.village_city,
            pincode=payload.pincode,
            address_line1=payload.address_line1,
            notes=payload.notes,
            hatch_date=payload.hatch_date,
            birds_placed=payload.birds_placed,
            proprietor=payload.proprietor,
        )
        await self._repo.add_unit(unit)
        await self._db.commit()
        await self._db.refresh(unit)
        log.info(
            'mitra_unit_created',
            user_id=str(owner_user_id),
            unit_id=str(unit.id),
            farm_type=unit.farm_type,
        )
        return FarmUnitResponse.model_validate(unit)

    async def get_unit(self, owner_user_id: uuid.UUID, unit_id: uuid.UUID) -> FarmUnitResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        return FarmUnitResponse.model_validate(unit)

    async def update_unit(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, payload: FarmUnitUpdateRequest
    ) -> FarmUnitResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        data = payload.model_dump(exclude_unset=True)
        if not data:
            return FarmUnitResponse.model_validate(unit)
        for key, value in data.items():
            setattr(unit, key, value)
        await self._db.flush()
        await self._db.commit()
        await self._db.refresh(unit)
        log.info('mitra_unit_updated', user_id=str(owner_user_id), unit_id=str(unit_id), fields=sorted(data))
        return FarmUnitResponse.model_validate(unit)

    async def delete_unit(self, owner_user_id: uuid.UUID, unit_id: uuid.UUID) -> None:
        unit = await self._require_unit(owner_user_id, unit_id)
        await self._repo.soft_delete_unit(unit)
        await self._db.commit()
        log.info('mitra_unit_deleted', user_id=str(owner_user_id), unit_id=str(unit_id))

    async def list_daily_records(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        *,
        from_date: date | None,
        to_date: date | None,
        limit: int,
        offset: int,
    ) -> DailyRecordListResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._require_daily_type(unit)
        self._validate_date_range(from_date, to_date)
        rows, total = await self._repo.list_daily_records(
            unit.id, from_date=from_date, to_date=to_date, limit=limit, offset=offset
        )
        return DailyRecordListResponse(
            items=[self._daily_response(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def upsert_daily_record(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, payload: DailyRecordUpsertRequest
    ) -> DailyRecordResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        farm_type = FarmUnitType(unit.farm_type)
        self._require_daily_type(unit)
        try:
            clean = validate_daily_data(farm_type, payload.data)
        except ValidationError as exc:
            raise _record_validation_error(exc) from None
        except ValueError as exc:
            raise AppError(str(exc), code='mitra_wrong_record_kind') from None
        existing = await self._repo.get_daily_by_date(unit.id, payload.record_date)
        if existing is None:
            row = MitraDailyRecord(unit_id=unit.id, record_date=payload.record_date, data=clean)
            await self._repo.add_daily(row)
        else:
            existing.data = clean
            row = existing
        await self._db.commit()
        await self._db.refresh(row)
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_daily_upserted',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            record_date=str(payload.record_date),
        )
        return self._daily_response(row)

    async def update_daily_record(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        record_id: uuid.UUID,
        payload: DailyRecordUpsertRequest,
    ) -> DailyRecordResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        farm_type = FarmUnitType(unit.farm_type)
        self._require_daily_type(unit)
        row = await self._repo.get_daily_record(unit.id, record_id)
        if row is None:
            raise NotFoundError('Record not found', code='mitra_record_not_found')
        try:
            clean = validate_daily_data(farm_type, payload.data)
        except ValidationError as exc:
            raise _record_validation_error(exc) from None
        except ValueError as exc:
            raise AppError(str(exc), code='mitra_wrong_record_kind') from None
        if payload.record_date != row.record_date:
            clash = await self._repo.get_daily_by_date(unit.id, payload.record_date)
            if clash is not None and clash.id != row.id:
                raise ConflictError(
                    'A record for this date already exists on this unit',
                    code='mitra_record_date_conflict',
                )
        row.record_date = payload.record_date
        row.data = clean
        await self._db.commit()
        await self._db.refresh(row)
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_daily_updated',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            record_id=str(record_id),
        )
        return self._daily_response(row)

    async def delete_daily_record(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, record_id: uuid.UUID
    ) -> None:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._require_daily_type(unit)
        row = await self._repo.get_daily_record(unit.id, record_id)
        if row is None:
            raise NotFoundError('Record not found', code='mitra_record_not_found')
        await self._repo.delete_daily(row)
        await self._db.commit()
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_daily_deleted',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            record_id=str(record_id),
        )

    async def list_hatchery_batches(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> HatcheryBatchListResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._require_hatchery_type(unit)
        rows, total = await self._repo.list_hatchery_batches(unit.id, limit=limit, offset=offset)
        return HatcheryBatchListResponse(
            items=[self._batch_response(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_hatchery_batch(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, payload: HatcheryBatchUpsertRequest
    ) -> HatcheryBatchResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._require_hatchery_type(unit)
        try:
            clean = validate_hatchery_batch(payload.data)
        except ValidationError as exc:
            raise _record_validation_error(exc, hatchery=True) from None
        batch_code = (payload.batch_code or clean.get('batch_code') or '').strip()
        if not batch_code:
            raise AppError('batch_code is required', code='mitra_batch_code_required')
        clean['batch_code'] = batch_code
        if await self._repo.get_hatchery_by_code(unit.id, batch_code):
            raise ConflictError(
                'Batch code already exists for this hatchery unit',
                code='mitra_batch_duplicate',
            )
        row = MitraHatcheryBatch(unit_id=unit.id, batch_code=batch_code, data=clean)
        await self._repo.add_hatchery_batch(row)
        await self._db.commit()
        await self._db.refresh(row)
        log.info(
            'mitra_hatchery_batch_created',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            batch_code=batch_code,
        )
        return self._batch_response(row)

    async def update_hatchery_batch(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        batch_id: uuid.UUID,
        payload: HatcheryBatchUpsertRequest,
    ) -> HatcheryBatchResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._require_hatchery_type(unit)
        row = await self._repo.get_hatchery_batch(unit.id, batch_id)
        if row is None:
            raise NotFoundError('Batch not found', code='mitra_batch_not_found')
        try:
            clean = validate_hatchery_batch(payload.data)
        except ValidationError as exc:
            raise _record_validation_error(exc, hatchery=True) from None
        batch_code = (payload.batch_code or clean.get('batch_code') or row.batch_code).strip()
        clean['batch_code'] = batch_code
        if batch_code != row.batch_code and await self._repo.get_hatchery_by_code(unit.id, batch_code):
            raise ConflictError(
                'Batch code already exists for this hatchery unit',
                code='mitra_batch_duplicate',
            )
        row.batch_code = batch_code
        row.data = clean
        await self._db.commit()
        await self._db.refresh(row)
        return self._batch_response(row)

    async def delete_hatchery_batch(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, batch_id: uuid.UUID
    ) -> None:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._require_hatchery_type(unit)
        row = await self._repo.get_hatchery_batch(unit.id, batch_id)
        if row is None:
            raise NotFoundError('Batch not found', code='mitra_batch_not_found')
        await self._repo.delete_hatchery_batch(row)
        await self._db.commit()

    async def _require_unit(self, owner_user_id: uuid.UUID, unit_id: uuid.UUID) -> MitraFarmUnit:
        unit = await self._repo.get_unit_for_owner(owner_user_id, unit_id)
        if unit is None:
            raise NotFoundError('Farm or shed not found', code='mitra_unit_not_found')
        return unit

    @staticmethod
    def _require_daily_type(unit: MitraFarmUnit) -> None:
        if FarmUnitType(unit.farm_type) not in DAILY_RECORD_TYPES:
            raise AppError(
                'This unit type uses hatchery batches, not daily flock rows',
                code='mitra_wrong_record_kind',
            )

    @staticmethod
    def _require_hatchery_type(unit: MitraFarmUnit) -> None:
        if FarmUnitType(unit.farm_type) is not FarmUnitType.HATCHERY:
            raise AppError(
                'Daily flock records are used for this unit type; hatchery batches are not',
                code='mitra_wrong_record_kind',
            )

    @staticmethod
    def _validate_date_range(from_date: date | None, to_date: date | None) -> None:
        if from_date is not None and to_date is not None and from_date > to_date:
            raise AppError('from_date must not be after to_date', code='mitra_invalid_date_range')

    @staticmethod
    def _daily_response(row: MitraDailyRecord) -> DailyRecordResponse:
        return DailyRecordResponse(
            id=row.id,
            unit_id=row.unit_id,
            record_date=row.record_date,
            data=row.data,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _batch_response(row: MitraHatcheryBatch) -> HatcheryBatchResponse:
        return HatcheryBatchResponse(
            id=row.id,
            unit_id=row.unit_id,
            batch_code=row.batch_code,
            data=row.data,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
