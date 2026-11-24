from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permissions
from app.core.rate_limit import limit
from app.db.session import get_db
from app.modules.mitra.billing_schemas import (
    BillingSaleCreateRequest,
    BillingSaleListResponse,
    BillingSaleResponse,
    BillingSaleUpdateRequest,
    ExpenseCreateRequest,
    ExpenseListResponse,
    ExpenseResponse,
    ExpenseUpdateRequest,
    MitraDashboardResponse,
    ProfitSummaryResponse,
)
from app.modules.mitra.gst_schemas import (
    GstRateSlabCreate,
    GstRateSlabListResponse,
    GstRateSlabResponse,
    GstRateSlabUpdate,
    UnitBillingSettingsResponse,
    UnitBillingSettingsUpsert,
)
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
from app.modules.mitra.service import MitraService

router = APIRouter(prefix='/mitra', tags=['mitra'])

DbDep = Annotated[AsyncSession, Depends(get_db)]
MitraReader = Annotated[CurrentUser, Depends(require_permissions('mitra:read'))]
MitraWriter = Annotated[CurrentUser, Depends(require_permissions('mitra:write'))]


async def get_mitra_service(db: DbDep) -> MitraService:
    return MitraService(db)


MitraS = Annotated[MitraService, Depends(get_mitra_service)]
MitraReadLimit = [Depends(limit('mitra:read'))]
MitraWriteLimit = [Depends(limit('mitra:write'))]
MitraExportLimit = [Depends(limit('mitra:export'))]


@router.get('/dashboard', dependencies=MitraReadLimit)
async def mitra_dashboard(
    user: MitraReader,
    mitra: MitraS,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    recent_limit: int = Query(default=20, ge=1, le=100),
) -> MitraDashboardResponse:
    """Owner dashboard: all farms, sales/expense totals, and recent sales feed."""
    return await mitra.owner_dashboard(
        user.user_id, from_date=from_date, to_date=to_date, recent_limit=recent_limit
    )


@router.get('/units', dependencies=MitraReadLimit)
async def list_farm_units(user: MitraReader, mitra: MitraS) -> FarmUnitListResponse:
    return await mitra.list_units(user.user_id)


@router.post('/units', status_code=201, dependencies=MitraWriteLimit)
async def create_farm_unit(
    payload: FarmUnitCreateRequest, user: MitraWriter, mitra: MitraS
) -> FarmUnitResponse:
    return await mitra.create_unit(user.user_id, payload)


@router.get('/units/{unit_id}', dependencies=MitraReadLimit)
async def get_farm_unit(unit_id: uuid.UUID, user: MitraReader, mitra: MitraS) -> FarmUnitResponse:
    return await mitra.get_unit(user.user_id, unit_id)


@router.patch('/units/{unit_id}', dependencies=MitraWriteLimit)
async def update_farm_unit(
    unit_id: uuid.UUID, payload: FarmUnitUpdateRequest, user: MitraWriter, mitra: MitraS
) -> FarmUnitResponse:
    return await mitra.update_unit(user.user_id, unit_id, payload)


@router.delete('/units/{unit_id}', status_code=204, dependencies=MitraWriteLimit)
async def delete_farm_unit(unit_id: uuid.UUID, user: MitraWriter, mitra: MitraS) -> Response:
    await mitra.delete_unit(user.user_id, unit_id)
    return Response(status_code=204)


@router.get('/units/{unit_id}/daily-records', dependencies=MitraReadLimit)
async def list_daily_records(
    unit_id: uuid.UUID,
    user: MitraReader,
    mitra: MitraS,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    limit: int = Query(default=90, ge=1, le=366),
    offset: int = Query(default=0, ge=0),
) -> DailyRecordListResponse:
    return await mitra.list_daily_records(
        user.user_id, unit_id, from_date=from_date, to_date=to_date, limit=limit, offset=offset
    )


@router.put('/units/{unit_id}/daily-records', dependencies=MitraWriteLimit)
async def upsert_daily_record(
    unit_id: uuid.UUID, payload: DailyRecordUpsertRequest, user: MitraWriter, mitra: MitraS
) -> DailyRecordResponse:
    return await mitra.upsert_daily_record(user.user_id, unit_id, payload)


@router.patch('/units/{unit_id}/daily-records/{record_id}', dependencies=MitraWriteLimit)
async def update_daily_record(
    unit_id: uuid.UUID,
    record_id: uuid.UUID,
    payload: DailyRecordUpsertRequest,
    user: MitraWriter,
    mitra: MitraS,
) -> DailyRecordResponse:
    return await mitra.update_daily_record(user.user_id, unit_id, record_id, payload)


@router.delete(
    '/units/{unit_id}/daily-records/{record_id}',
    status_code=204,
    dependencies=MitraWriteLimit,
)
async def delete_daily_record(
    unit_id: uuid.UUID, record_id: uuid.UUID, user: MitraWriter, mitra: MitraS
) -> Response:
    await mitra.delete_daily_record(user.user_id, unit_id, record_id)
    return Response(status_code=204)


@router.get('/units/{unit_id}/hatchery-batches', dependencies=MitraReadLimit)
async def list_hatchery_batches(
    unit_id: uuid.UUID,
    user: MitraReader,
    mitra: MitraS,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> HatcheryBatchListResponse:
    return await mitra.list_hatchery_batches(user.user_id, unit_id, limit=limit, offset=offset)


@router.post(
    '/units/{unit_id}/hatchery-batches',
    status_code=201,
    dependencies=MitraWriteLimit,
)
async def create_hatchery_batch(
    unit_id: uuid.UUID, payload: HatcheryBatchUpsertRequest, user: MitraWriter, mitra: MitraS
) -> HatcheryBatchResponse:
    return await mitra.create_hatchery_batch(user.user_id, unit_id, payload)


@router.patch('/units/{unit_id}/hatchery-batches/{batch_id}', dependencies=MitraWriteLimit)
async def update_hatchery_batch(
    unit_id: uuid.UUID,
    batch_id: uuid.UUID,
    payload: HatcheryBatchUpsertRequest,
    user: MitraWriter,
    mitra: MitraS,
) -> HatcheryBatchResponse:
    return await mitra.update_hatchery_batch(user.user_id, unit_id, batch_id, payload)


@router.delete(
    '/units/{unit_id}/hatchery-batches/{batch_id}',
    status_code=204,
    dependencies=MitraWriteLimit,
)
async def delete_hatchery_batch(
    unit_id: uuid.UUID, batch_id: uuid.UUID, user: MitraWriter, mitra: MitraS
) -> Response:
    await mitra.delete_hatchery_batch(user.user_id, unit_id, batch_id)
    return Response(status_code=204)


@router.get('/units/{unit_id}/billing-settings', dependencies=MitraReadLimit)
async def get_billing_settings(
    unit_id: uuid.UUID, user: MitraReader, mitra: MitraS
) -> UnitBillingSettingsResponse:
    return await mitra.get_billing_settings(user.user_id, unit_id)


@router.put('/units/{unit_id}/billing-settings', dependencies=MitraWriteLimit)
async def upsert_billing_settings(
    unit_id: uuid.UUID,
    payload: UnitBillingSettingsUpsert,
    user: MitraWriter,
    mitra: MitraS,
) -> UnitBillingSettingsResponse:
    return await mitra.upsert_billing_settings(user.user_id, unit_id, payload)


@router.get('/gst-rate-slabs', dependencies=MitraReadLimit)
async def list_gst_rate_slabs(
    user: MitraReader,
    mitra: MitraS,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    active_only: bool = Query(default=True),
) -> GstRateSlabListResponse:
    return await mitra.list_gst_slabs(
        user.user_id, limit=limit, offset=offset, active_only=active_only
    )


@router.post('/gst-rate-slabs', status_code=201, dependencies=MitraWriteLimit)
async def create_gst_rate_slab(
    payload: GstRateSlabCreate, user: MitraWriter, mitra: MitraS
) -> GstRateSlabResponse:
    return await mitra.create_gst_slab(user.user_id, payload)


@router.patch('/gst-rate-slabs/{slab_id}', dependencies=MitraWriteLimit)
async def update_gst_rate_slab(
    slab_id: uuid.UUID,
    payload: GstRateSlabUpdate,
    user: MitraWriter,
    mitra: MitraS,
) -> GstRateSlabResponse:
    return await mitra.update_gst_slab(user.user_id, slab_id, payload)


@router.delete('/gst-rate-slabs/{slab_id}', status_code=204, dependencies=MitraWriteLimit)
async def delete_gst_rate_slab(
    slab_id: uuid.UUID, user: MitraWriter, mitra: MitraS
) -> Response:
    await mitra.delete_gst_slab(user.user_id, slab_id)
    return Response(status_code=204)


@router.get('/units/{unit_id}/billing', dependencies=MitraReadLimit)
async def list_billing(
    unit_id: uuid.UUID,
    user: MitraReader,
    mitra: MitraS,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> BillingSaleListResponse:
    return await mitra.list_billing_sales(
        user.user_id, unit_id, from_date=from_date, to_date=to_date, limit=limit, offset=offset
    )


@router.post('/units/{unit_id}/billing', status_code=201, dependencies=MitraWriteLimit)
async def create_billing(
    unit_id: uuid.UUID, payload: BillingSaleCreateRequest, user: MitraWriter, mitra: MitraS
) -> BillingSaleResponse:
    return await mitra.create_billing_sale(user.user_id, unit_id, payload)


@router.patch('/units/{unit_id}/billing/{sale_id}', dependencies=MitraWriteLimit)
async def update_billing(
    unit_id: uuid.UUID,
    sale_id: uuid.UUID,
    payload: BillingSaleUpdateRequest,
    user: MitraWriter,
    mitra: MitraS,
) -> BillingSaleResponse:
    return await mitra.update_billing_sale(user.user_id, unit_id, sale_id, payload)


@router.delete('/units/{unit_id}/billing/{sale_id}', status_code=204, dependencies=MitraWriteLimit)
async def delete_billing(
    unit_id: uuid.UUID, sale_id: uuid.UUID, user: MitraWriter, mitra: MitraS
) -> Response:
    await mitra.delete_billing_sale(user.user_id, unit_id, sale_id)
    return Response(status_code=204)


@router.get('/units/{unit_id}/expenses', dependencies=MitraReadLimit)
async def list_expenses(
    unit_id: uuid.UUID,
    user: MitraReader,
    mitra: MitraS,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ExpenseListResponse:
    return await mitra.list_expenses(
        user.user_id, unit_id, from_date=from_date, to_date=to_date, limit=limit, offset=offset
    )


@router.post('/units/{unit_id}/expenses', status_code=201, dependencies=MitraWriteLimit)
async def create_expense(
    unit_id: uuid.UUID, payload: ExpenseCreateRequest, user: MitraWriter, mitra: MitraS
) -> ExpenseResponse:
    return await mitra.create_expense(user.user_id, unit_id, payload)


@router.patch('/units/{unit_id}/expenses/{expense_id}', dependencies=MitraWriteLimit)
async def update_expense(
    unit_id: uuid.UUID,
    expense_id: uuid.UUID,
    payload: ExpenseUpdateRequest,
    user: MitraWriter,
    mitra: MitraS,
) -> ExpenseResponse:
    return await mitra.update_expense(user.user_id, unit_id, expense_id, payload)


@router.delete('/units/{unit_id}/expenses/{expense_id}', status_code=204, dependencies=MitraWriteLimit)
async def delete_expense(
    unit_id: uuid.UUID, expense_id: uuid.UUID, user: MitraWriter, mitra: MitraS
) -> Response:
    await mitra.delete_expense(user.user_id, unit_id, expense_id)
    return Response(status_code=204)


@router.get('/units/{unit_id}/profit-summary', dependencies=MitraReadLimit)
async def profit_summary(
    unit_id: uuid.UUID,
    user: MitraReader,
    mitra: MitraS,
    flock_batch_ref: str | None = Query(default=None, max_length=64),
) -> ProfitSummaryResponse:
    return await mitra.profit_summary(user.user_id, unit_id, flock_batch_ref=flock_batch_ref)


@router.get('/units/{unit_id}/export', dependencies=[*MitraReadLimit, *MitraExportLimit])
async def export_unit(
    unit_id: uuid.UUID,
    user: MitraReader,
    mitra: MitraS,
    export_format: str = Query(default='csv', pattern='^(csv|json)$', alias='format'),
    include_daily: bool = Query(default=True),
) -> Response:
    return await mitra.export_unit(
        user.user_id, unit_id, export_format=export_format, include_daily=include_daily
    )
