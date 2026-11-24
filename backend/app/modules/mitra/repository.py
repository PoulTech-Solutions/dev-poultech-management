from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mitra import (
    MitraBillingSale,
    MitraDailyRecord,
    MitraExpense,
    MitraFarmUnit,
    MitraGstRateSlab,
    MitraHatcheryBatch,
    MitraUnitBillingSettings,
)
from app.db.models.users import User

_MITRA_UNIT_CAP_LOCK_NS = 'mitra_unit_cap'


class MitraRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def lock_owner_unit_cap(self, owner_user_id: uuid.UUID) -> None:
        """Serialize create-unit for this owner (cap has no DB unique). Released on commit/rollback."""
        await self.db.execute(
            text('SELECT pg_advisory_xact_lock(hashtext(:ns), hashtext(:owner))'),
            {'ns': _MITRA_UNIT_CAP_LOCK_NS, 'owner': str(owner_user_id)},
        )

    async def count_active_units(self, owner_user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(MitraFarmUnit)
            .where(MitraFarmUnit.owner_user_id == owner_user_id, MitraFarmUnit.deleted_at.is_(None))
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def list_units(self, owner_user_id: uuid.UUID) -> list[MitraFarmUnit]:
        stmt = (
            select(MitraFarmUnit)
            .where(MitraFarmUnit.owner_user_id == owner_user_id, MitraFarmUnit.deleted_at.is_(None))
            .order_by(MitraFarmUnit.created_at.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_unit_for_owner(self, owner_user_id: uuid.UUID, unit_id: uuid.UUID) -> MitraFarmUnit | None:
        stmt = select(MitraFarmUnit).where(
            MitraFarmUnit.id == unit_id,
            MitraFarmUnit.owner_user_id == owner_user_id,
            MitraFarmUnit.deleted_at.is_(None),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_unit(self, unit: MitraFarmUnit) -> MitraFarmUnit:
        self.db.add(unit)
        await self.db.flush()
        return unit

    async def soft_delete_unit(self, unit: MitraFarmUnit) -> None:
        from datetime import UTC, datetime

        unit.deleted_at = datetime.now(UTC)

    async def list_daily_records(
        self,
        unit_id: uuid.UUID,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 366,
        offset: int = 0,
    ) -> tuple[list[MitraDailyRecord], int]:
        filters = [MitraDailyRecord.unit_id == unit_id]
        if from_date is not None:
            filters.append(MitraDailyRecord.record_date >= from_date)
        if to_date is not None:
            filters.append(MitraDailyRecord.record_date <= to_date)
        count_stmt = select(func.count()).select_from(MitraDailyRecord).where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())
        stmt = (
            select(MitraDailyRecord)
            .where(*filters)
            .order_by(MitraDailyRecord.record_date.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        return rows, total

    async def get_daily_record(self, unit_id: uuid.UUID, record_id: uuid.UUID) -> MitraDailyRecord | None:
        stmt = select(MitraDailyRecord).where(
            MitraDailyRecord.id == record_id, MitraDailyRecord.unit_id == unit_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_daily_by_date(self, unit_id: uuid.UUID, record_date: date) -> MitraDailyRecord | None:
        stmt = select(MitraDailyRecord).where(
            MitraDailyRecord.unit_id == unit_id, MitraDailyRecord.record_date == record_date
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_daily(self, row: MitraDailyRecord) -> MitraDailyRecord:
        self.db.add(row)
        await self.db.flush()
        return row

    async def delete_daily(self, row: MitraDailyRecord) -> None:
        await self.db.delete(row)

    async def list_hatchery_batches(
        self,
        unit_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MitraHatcheryBatch], int]:
        filters = [MitraHatcheryBatch.unit_id == unit_id]
        count_stmt = select(func.count()).select_from(MitraHatcheryBatch).where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())
        stmt = (
            select(MitraHatcheryBatch)
            .where(*filters)
            .order_by(MitraHatcheryBatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        return rows, total

    async def get_hatchery_batch(self, unit_id: uuid.UUID, batch_id: uuid.UUID) -> MitraHatcheryBatch | None:
        stmt = select(MitraHatcheryBatch).where(
            MitraHatcheryBatch.id == batch_id, MitraHatcheryBatch.unit_id == unit_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_hatchery_by_code(self, unit_id: uuid.UUID, batch_code: str) -> MitraHatcheryBatch | None:
        stmt = select(MitraHatcheryBatch).where(
            MitraHatcheryBatch.unit_id == unit_id, MitraHatcheryBatch.batch_code == batch_code
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_hatchery_batch(self, row: MitraHatcheryBatch) -> MitraHatcheryBatch:
        self.db.add(row)
        await self.db.flush()
        return row

    async def delete_hatchery_batch(self, row: MitraHatcheryBatch) -> None:
        await self.db.delete(row)

    async def list_billing_sales(
        self,
        unit_id: uuid.UUID,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MitraBillingSale], int]:
        filters = [MitraBillingSale.unit_id == unit_id]
        if from_date is not None:
            filters.append(MitraBillingSale.sale_date >= from_date)
        if to_date is not None:
            filters.append(MitraBillingSale.sale_date <= to_date)
        count_stmt = select(func.count()).select_from(MitraBillingSale).where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())
        stmt = (
            select(MitraBillingSale)
            .where(*filters)
            .order_by(MitraBillingSale.sale_date.desc(), MitraBillingSale.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.db.execute(stmt)).scalars().all()), total

    async def get_billing_sale(self, unit_id: uuid.UUID, sale_id: uuid.UUID) -> MitraBillingSale | None:
        stmt = select(MitraBillingSale).where(
            MitraBillingSale.id == sale_id, MitraBillingSale.unit_id == unit_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_billing_by_invoice(self, unit_id: uuid.UUID, invoice_no: str) -> MitraBillingSale | None:
        stmt = select(MitraBillingSale).where(
            MitraBillingSale.unit_id == unit_id, MitraBillingSale.invoice_no == invoice_no
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_billing_sale(self, row: MitraBillingSale) -> MitraBillingSale:
        self.db.add(row)
        await self.db.flush()
        return row

    async def delete_billing_sale(self, row: MitraBillingSale) -> None:
        await self.db.delete(row)

    async def list_expenses(
        self,
        unit_id: uuid.UUID,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MitraExpense], int]:
        filters = [MitraExpense.unit_id == unit_id]
        if from_date is not None:
            filters.append(MitraExpense.expense_date >= from_date)
        if to_date is not None:
            filters.append(MitraExpense.expense_date <= to_date)
        count_stmt = select(func.count()).select_from(MitraExpense).where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())
        stmt = (
            select(MitraExpense)
            .where(*filters)
            .order_by(MitraExpense.expense_date.desc(), MitraExpense.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.db.execute(stmt)).scalars().all()), total

    async def get_expense(self, unit_id: uuid.UUID, expense_id: uuid.UUID) -> MitraExpense | None:
        stmt = select(MitraExpense).where(MitraExpense.id == expense_id, MitraExpense.unit_id == unit_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_expense_by_bill(self, unit_id: uuid.UUID, bill_no: str) -> MitraExpense | None:
        stmt = select(MitraExpense).where(MitraExpense.unit_id == unit_id, MitraExpense.bill_no == bill_no)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_expense(self, row: MitraExpense) -> MitraExpense:
        self.db.add(row)
        await self.db.flush()
        return row

    async def delete_expense(self, row: MitraExpense) -> None:
        await self.db.delete(row)

    async def profit_totals(
        self, unit_id: uuid.UUID, *, flock_batch_ref: str | None = None
    ) -> dict[str, Decimal]:
        sale_filters = [MitraBillingSale.unit_id == unit_id]
        exp_filters = [MitraExpense.unit_id == unit_id]
        if flock_batch_ref is not None:
            sale_filters.append(MitraBillingSale.flock_batch_ref == flock_batch_ref)
            exp_filters.append(MitraExpense.flock_batch_ref == flock_batch_ref)

        income = (
            await self.db.execute(
                select(func.coalesce(func.sum(MitraBillingSale.amount_inr), 0)).where(*sale_filters)
            )
        ).scalar_one()
        to_receive = (
            await self.db.execute(
                select(func.coalesce(func.sum(MitraBillingSale.balance_inr), 0)).where(*sale_filters)
            )
        ).scalar_one()
        units_sold_stmt = select(func.coalesce(func.sum(MitraBillingSale.quantity), 0)).where(*sale_filters)
        units_sold = (await self.db.execute(units_sold_stmt)).scalar_one()
        expenses_stmt = select(func.coalesce(func.sum(MitraExpense.amount_inr), 0)).where(*exp_filters)
        expenses = (await self.db.execute(expenses_stmt)).scalar_one()
        to_pay_stmt = select(func.coalesce(func.sum(MitraExpense.balance_inr), 0)).where(*exp_filters)
        to_pay = (await self.db.execute(to_pay_stmt)).scalar_one()

        return {
            'income_inr': Decimal(str(income)),
            'expenses_inr': Decimal(str(expenses)),
            'to_receive_inr': Decimal(str(to_receive)),
            'to_pay_inr': Decimal(str(to_pay)),
            'units_sold': Decimal(str(units_sold)),
        }

    async def list_all_billing_for_export(
        self, unit_id: uuid.UUID, *, limit: int = 5000
    ) -> list[MitraBillingSale]:
        stmt = (
            select(MitraBillingSale)
            .where(MitraBillingSale.unit_id == unit_id)
            .order_by(MitraBillingSale.sale_date.asc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_all_expenses_for_export(
        self, unit_id: uuid.UUID, *, limit: int = 5000
    ) -> list[MitraExpense]:
        stmt = (
            select(MitraExpense)
            .where(MitraExpense.unit_id == unit_id)
            .order_by(MitraExpense.expense_date.asc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_billing_settings(self, unit_id: uuid.UUID) -> MitraUnitBillingSettings | None:
        stmt = select(MitraUnitBillingSettings).where(MitraUnitBillingSettings.unit_id == unit_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_billing_settings(self, row: MitraUnitBillingSettings) -> MitraUnitBillingSettings:
        self.db.add(row)
        await self.db.flush()
        return row

    async def list_gst_slabs(
        self, owner_user_id: uuid.UUID, *, limit: int, offset: int, active_only: bool
    ) -> tuple[list[MitraGstRateSlab], int]:
        filters = [MitraGstRateSlab.owner_user_id == owner_user_id]
        if active_only:
            filters.append(MitraGstRateSlab.is_active.is_(True))
        count_stmt = select(func.count()).select_from(MitraGstRateSlab).where(*filters)
        total = int((await self.db.execute(count_stmt)).scalar_one())
        stmt = (
            select(MitraGstRateSlab)
            .where(*filters)
            .order_by(MitraGstRateSlab.hsn_sac.asc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.db.execute(stmt)).scalars().all()), total

    async def get_gst_slab(self, owner_user_id: uuid.UUID, slab_id: uuid.UUID) -> MitraGstRateSlab | None:
        stmt = select(MitraGstRateSlab).where(
            MitraGstRateSlab.id == slab_id,
            MitraGstRateSlab.owner_user_id == owner_user_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_gst_slab_by_hsn(self, owner_user_id: uuid.UUID, hsn_sac: str) -> MitraGstRateSlab | None:
        key = hsn_sac.strip()
        stmt = select(MitraGstRateSlab).where(
            MitraGstRateSlab.owner_user_id == owner_user_id,
            MitraGstRateSlab.hsn_sac == key,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_gst_slab(self, row: MitraGstRateSlab) -> MitraGstRateSlab:
        self.db.add(row)
        await self.db.flush()
        return row

    async def owner_dashboard_by_unit(
        self,
        owner_user_id: uuid.UUID,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict]:
        """Per-farm income/expense rollups for the owner dashboard."""
        units = await self.list_units(owner_user_id)
        if not units:
            return []

        unit_ids = [u.id for u in units]
        sale_filters = [MitraBillingSale.unit_id.in_(unit_ids)]
        exp_filters = [MitraExpense.unit_id.in_(unit_ids)]
        if from_date is not None:
            sale_filters.append(MitraBillingSale.sale_date >= from_date)
            exp_filters.append(MitraExpense.expense_date >= from_date)
        if to_date is not None:
            sale_filters.append(MitraBillingSale.sale_date <= to_date)
            exp_filters.append(MitraExpense.expense_date <= to_date)

        sale_rows = (
            await self.db.execute(
                select(
                    MitraBillingSale.unit_id,
                    func.count().label('sale_count'),
                    func.coalesce(func.sum(MitraBillingSale.amount_inr), 0).label('income_inr'),
                    func.coalesce(func.sum(MitraBillingSale.balance_inr), 0).label('to_receive_inr'),
                    func.coalesce(func.sum(MitraBillingSale.quantity), 0).label('units_sold'),
                )
                .where(*sale_filters)
                .group_by(MitraBillingSale.unit_id)
            )
        ).all()
        sale_map = {row.unit_id: row for row in sale_rows}

        exp_rows = (
            await self.db.execute(
                select(
                    MitraExpense.unit_id,
                    func.count().label('expense_count'),
                    func.coalesce(func.sum(MitraExpense.amount_inr), 0).label('expenses_inr'),
                    func.coalesce(func.sum(MitraExpense.balance_inr), 0).label('to_pay_inr'),
                )
                .where(*exp_filters)
                .group_by(MitraExpense.unit_id)
            )
        ).all()
        exp_map = {row.unit_id: row for row in exp_rows}

        cards: list[dict] = []
        for unit in units:
            sale = sale_map.get(unit.id)
            exp = exp_map.get(unit.id)
            income = Decimal(str(sale.income_inr)) if sale else Decimal('0')
            expenses = Decimal(str(exp.expenses_inr)) if exp else Decimal('0')
            cards.append(
                {
                    'unit_id': unit.id,
                    'unit_name': unit.name,
                    'farm_type': unit.farm_type,
                    'state': unit.state,
                    'district': unit.district,
                    'income_inr': income,
                    'expenses_inr': expenses,
                    'profit_inr': income - expenses,
                    'to_receive_inr': Decimal(str(sale.to_receive_inr)) if sale else Decimal('0'),
                    'to_pay_inr': Decimal(str(exp.to_pay_inr)) if exp else Decimal('0'),
                    'units_sold': Decimal(str(sale.units_sold)) if sale else Decimal('0'),
                    'sale_count': int(sale.sale_count) if sale else 0,
                    'expense_count': int(exp.expense_count) if exp else 0,
                }
            )
        return cards

    async def owner_recent_sales(
        self,
        owner_user_id: uuid.UUID,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 20,
    ) -> list[tuple[MitraBillingSale, MitraFarmUnit]]:
        """Latest sale lines across all of the owner's farms."""
        filters = [
            MitraFarmUnit.owner_user_id == owner_user_id,
            MitraFarmUnit.deleted_at.is_(None),
        ]
        if from_date is not None:
            filters.append(MitraBillingSale.sale_date >= from_date)
        if to_date is not None:
            filters.append(MitraBillingSale.sale_date <= to_date)
        stmt = (
            select(MitraBillingSale, MitraFarmUnit)
            .join(MitraFarmUnit, MitraFarmUnit.id == MitraBillingSale.unit_id)
            .where(*filters)
            .order_by(MitraBillingSale.sale_date.desc(), MitraBillingSale.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).all())

    async def admin_dashboard_by_unit(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict]:
        """Per-farm rollups across every non-deleted Mitra unit (admin dashboard)."""
        unit_rows = (
            await self.db.execute(
                select(MitraFarmUnit, User.email)
                .join(User, User.id == MitraFarmUnit.owner_user_id)
                .where(MitraFarmUnit.deleted_at.is_(None))
                .order_by(MitraFarmUnit.created_at.asc())
            )
        ).all()
        if not unit_rows:
            return []

        unit_ids = [unit.id for unit, _email in unit_rows]
        sale_filters = [MitraBillingSale.unit_id.in_(unit_ids)]
        exp_filters = [MitraExpense.unit_id.in_(unit_ids)]
        if from_date is not None:
            sale_filters.append(MitraBillingSale.sale_date >= from_date)
            exp_filters.append(MitraExpense.expense_date >= from_date)
        if to_date is not None:
            sale_filters.append(MitraBillingSale.sale_date <= to_date)
            exp_filters.append(MitraExpense.expense_date <= to_date)

        sale_rows = (
            await self.db.execute(
                select(
                    MitraBillingSale.unit_id,
                    func.count().label('sale_count'),
                    func.coalesce(func.sum(MitraBillingSale.amount_inr), 0).label('income_inr'),
                    func.coalesce(func.sum(MitraBillingSale.balance_inr), 0).label('to_receive_inr'),
                    func.coalesce(func.sum(MitraBillingSale.quantity), 0).label('units_sold'),
                )
                .where(*sale_filters)
                .group_by(MitraBillingSale.unit_id)
            )
        ).all()
        sale_map = {row.unit_id: row for row in sale_rows}

        exp_rows = (
            await self.db.execute(
                select(
                    MitraExpense.unit_id,
                    func.count().label('expense_count'),
                    func.coalesce(func.sum(MitraExpense.amount_inr), 0).label('expenses_inr'),
                    func.coalesce(func.sum(MitraExpense.balance_inr), 0).label('to_pay_inr'),
                )
                .where(*exp_filters)
                .group_by(MitraExpense.unit_id)
            )
        ).all()
        exp_map = {row.unit_id: row for row in exp_rows}

        cards: list[dict] = []
        for unit, owner_email in unit_rows:
            sale = sale_map.get(unit.id)
            exp = exp_map.get(unit.id)
            income = Decimal(str(sale.income_inr)) if sale else Decimal('0')
            expenses = Decimal(str(exp.expenses_inr)) if exp else Decimal('0')
            cards.append(
                {
                    'unit_id': unit.id,
                    'unit_name': unit.name,
                    'farm_type': unit.farm_type,
                    'state': unit.state,
                    'district': unit.district,
                    'owner_user_id': unit.owner_user_id,
                    'owner_email': owner_email,
                    'income_inr': income,
                    'expenses_inr': expenses,
                    'profit_inr': income - expenses,
                    'to_receive_inr': Decimal(str(sale.to_receive_inr)) if sale else Decimal('0'),
                    'to_pay_inr': Decimal(str(exp.to_pay_inr)) if exp else Decimal('0'),
                    'units_sold': Decimal(str(sale.units_sold)) if sale else Decimal('0'),
                    'sale_count': int(sale.sale_count) if sale else 0,
                    'expense_count': int(exp.expense_count) if exp else 0,
                }
            )
        return cards

    async def admin_sales_by_unit(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        sales_limit: int = 50,
    ) -> dict[uuid.UUID, list[MitraBillingSale]]:
        """Latest sales per farm (windowed), capped at ``sales_limit`` lines each."""
        filters = [MitraFarmUnit.deleted_at.is_(None)]
        if from_date is not None:
            filters.append(MitraBillingSale.sale_date >= from_date)
        if to_date is not None:
            filters.append(MitraBillingSale.sale_date <= to_date)

        rn = func.row_number().over(
            partition_by=MitraBillingSale.unit_id,
            order_by=(MitraBillingSale.sale_date.desc(), MitraBillingSale.created_at.desc()),
        ).label('rn')
        ranked = (
            select(MitraBillingSale.id.label('sale_id'), rn)
            .join(MitraFarmUnit, MitraFarmUnit.id == MitraBillingSale.unit_id)
            .where(*filters)
            .subquery()
        )
        stmt = (
            select(MitraBillingSale)
            .join(ranked, MitraBillingSale.id == ranked.c.sale_id)
            .where(ranked.c.rn <= sales_limit)
            .order_by(
                MitraBillingSale.unit_id.asc(),
                MitraBillingSale.sale_date.desc(),
                MitraBillingSale.created_at.desc(),
            )
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        grouped: dict[uuid.UUID, list[MitraBillingSale]] = {}
        for sale in rows:
            grouped.setdefault(sale.unit_id, []).append(sale)
        return grouped
