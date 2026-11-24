"""Billing, expenses, profit summary, and unit export (MitraBillingMixin)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import structlog
from fastapi.responses import Response

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.db.models.mitra import MitraBillingSale, MitraExpense
from app.modules.mitra.billing_schemas import (
    BillingSaleCreateRequest,
    BillingSaleListResponse,
    BillingSaleResponse,
    BillingSaleUpdateRequest,
    ExpenseCreateRequest,
    ExpenseListResponse,
    ExpenseResponse,
    ExpenseUpdateRequest,
    MitraDashboardFarmCard,
    MitraDashboardRecentSale,
    MitraDashboardResponse,
    MitraDashboardTotals,
    ProfitSummaryResponse,
)
from app.modules.mitra.export import build_csv_export, build_json_export
from app.modules.mitra.export_cache import drop_export_cache, read_export_cache, write_export_cache

log = structlog.get_logger()


class MitraBillingMixin:
    """Mixed into MitraService — expects self._db, self._repo, _require_unit, _validate_date_range."""

    async def list_billing_sales(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        *,
        from_date: date | None,
        to_date: date | None,
        limit: int,
        offset: int,
    ) -> BillingSaleListResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._validate_date_range(from_date, to_date)
        rows, total = await self._repo.list_billing_sales(
            unit.id, from_date=from_date, to_date=to_date, limit=limit, offset=offset
        )
        return BillingSaleListResponse(
            items=[BillingSaleResponse.model_validate(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_billing_sale(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, payload: BillingSaleCreateRequest
    ) -> BillingSaleResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        # Multiple rows may share invoice_no (one row per line item on the same bill).
        settings = await self._repo.get_billing_settings(unit.id)
        gst_fields = await self._apply_gst_to_new_sale(owner_user_id, payload, settings)
        row = MitraBillingSale(
            unit_id=unit.id,
            invoice_no=payload.invoice_no,
            sale_date=payload.sale_date,
            flock_batch_ref=payload.flock_batch_ref,
            buyer=payload.buyer,
            item=payload.item,
            quantity=payload.quantity,
            qty_unit=payload.qty_unit,
            rate_inr=payload.rate_inr,
            pay_mode=payload.pay_mode,
            remarks=payload.remarks,
            **gst_fields,
        )
        await self._repo.add_billing_sale(row)
        await self._db.commit()
        await self._db.refresh(row)
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_billing_created',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            invoice_no=payload.invoice_no,
        )
        return BillingSaleResponse.model_validate(row)

    async def update_billing_sale(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        sale_id: uuid.UUID,
        payload: BillingSaleUpdateRequest,
    ) -> BillingSaleResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        row = await self._repo.get_billing_sale(unit.id, sale_id)
        if row is None:
            raise NotFoundError('Billing entry not found', code='mitra_billing_not_found')
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(row, key, value)
        settings = await self._repo.get_billing_settings(unit.id)
        await self._reapply_gst_on_sale_row(owner_user_id, row, settings)
        await self._db.commit()
        await self._db.refresh(row)
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_billing_updated',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            sale_id=str(sale_id),
        )
        return BillingSaleResponse.model_validate(row)

    async def delete_billing_sale(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, sale_id: uuid.UUID
    ) -> None:
        unit = await self._require_unit(owner_user_id, unit_id)
        row = await self._repo.get_billing_sale(unit.id, sale_id)
        if row is None:
            raise NotFoundError('Billing entry not found', code='mitra_billing_not_found')
        await self._repo.delete_billing_sale(row)
        await self._db.commit()
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_billing_deleted',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            sale_id=str(sale_id),
        )

    async def list_expenses(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        *,
        from_date: date | None,
        to_date: date | None,
        limit: int,
        offset: int,
    ) -> ExpenseListResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        self._validate_date_range(from_date, to_date)
        rows, total = await self._repo.list_expenses(
            unit.id, from_date=from_date, to_date=to_date, limit=limit, offset=offset
        )
        return ExpenseListResponse(
            items=[ExpenseResponse.model_validate(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_expense(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, payload: ExpenseCreateRequest
    ) -> ExpenseResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        bill_no = (payload.bill_no or '').strip() or None
        if bill_no and await self._repo.get_expense_by_bill(unit.id, bill_no):
            raise ConflictError(
                'Bill number already exists on this unit',
                code='mitra_expense_bill_duplicate',
            )
        row = MitraExpense(
            unit_id=unit.id,
            expense_date=payload.expense_date,
            bill_no=bill_no,
            flock_batch_ref=payload.flock_batch_ref,
            supplier=payload.supplier,
            category=payload.category,
            description=payload.description,
            amount_inr=payload.amount_inr,
            paid_inr=payload.paid_inr,
            balance_inr=payload.balance_inr,
            remarks=payload.remarks,
        )
        await self._repo.add_expense(row)
        await self._db.commit()
        await self._db.refresh(row)
        await drop_export_cache(owner_user_id, unit.id)
        log.info('mitra_expense_created', user_id=str(owner_user_id), unit_id=str(unit_id), bill_no=bill_no)
        return ExpenseResponse.model_validate(row)

    async def update_expense(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        expense_id: uuid.UUID,
        payload: ExpenseUpdateRequest,
    ) -> ExpenseResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        row = await self._repo.get_expense(unit.id, expense_id)
        if row is None:
            raise NotFoundError('Expense not found', code='mitra_expense_not_found')
        data = payload.model_dump(exclude_unset=True)
        if 'bill_no' in data:
            bill_no = (data['bill_no'] or '').strip() or None
            data['bill_no'] = bill_no
            if bill_no and bill_no != row.bill_no:
                clash = await self._repo.get_expense_by_bill(unit.id, bill_no)
                if clash is not None:
                    raise ConflictError(
                'Bill number already exists on this unit',
                code='mitra_expense_bill_duplicate',
            )
        for key, value in data.items():
            setattr(row, key, value)
        self._recompute_expense_balance(row)
        await self._db.commit()
        await self._db.refresh(row)
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_expense_updated',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            expense_id=str(expense_id),
        )
        return ExpenseResponse.model_validate(row)

    async def delete_expense(
        self, owner_user_id: uuid.UUID, unit_id: uuid.UUID, expense_id: uuid.UUID
    ) -> None:
        unit = await self._require_unit(owner_user_id, unit_id)
        row = await self._repo.get_expense(unit.id, expense_id)
        if row is None:
            raise NotFoundError('Expense not found', code='mitra_expense_not_found')
        await self._repo.delete_expense(row)
        await self._db.commit()
        await drop_export_cache(owner_user_id, unit.id)
        log.info(
            'mitra_expense_deleted',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            expense_id=str(expense_id),
        )

    async def profit_summary(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        *,
        flock_batch_ref: str | None = None,
    ) -> ProfitSummaryResponse:
        unit = await self._require_unit(owner_user_id, unit_id)
        totals = await self._repo.profit_totals(unit.id, flock_batch_ref=flock_batch_ref)
        income = totals['income_inr']
        expenses = totals['expenses_inr']
        profit = income - expenses
        units_sold = totals['units_sold']
        per_unit = (profit / units_sold).quantize(Decimal('0.01')) if units_sold > 0 else None
        return ProfitSummaryResponse(
            unit_id=unit.id,
            unit_name=unit.name,
            farm_type=unit.farm_type,
            flock_batch_ref=flock_batch_ref,
            income_inr=income,
            expenses_inr=expenses,
            profit_inr=profit,
            units_sold=units_sold,
            profit_per_unit_inr=per_unit,
            to_receive_inr=totals['to_receive_inr'],
            to_pay_inr=totals['to_pay_inr'],
        )

    async def owner_dashboard(
        self,
        owner_user_id: uuid.UUID,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        recent_limit: int = 20,
    ) -> MitraDashboardResponse:
        """All farms + sales rollups for the Mitra home dashboard (one round trip)."""
        self._validate_date_range(from_date, to_date)
        farm_rows = await self._repo.owner_dashboard_by_unit(
            owner_user_id, from_date=from_date, to_date=to_date
        )
        farms = [MitraDashboardFarmCard(**row) for row in farm_rows]
        income = sum((f.income_inr for f in farms), Decimal('0'))
        expenses = sum((f.expenses_inr for f in farms), Decimal('0'))
        recent_rows = await self._repo.owner_recent_sales(
            owner_user_id, from_date=from_date, to_date=to_date, limit=recent_limit
        )
        recent_sales = [
            MitraDashboardRecentSale(
                id=sale.id,
                unit_id=unit.id,
                unit_name=unit.name,
                farm_type=unit.farm_type,
                invoice_no=sale.invoice_no,
                sale_date=sale.sale_date,
                buyer=sale.buyer,
                item=sale.item,
                quantity=sale.quantity,
                qty_unit=sale.qty_unit,
                amount_inr=sale.amount_inr,
                received_inr=sale.received_inr,
                balance_inr=sale.balance_inr,
            )
            for sale, unit in recent_rows
        ]
        return MitraDashboardResponse(
            from_date=from_date,
            to_date=to_date,
            totals=MitraDashboardTotals(
                farm_count=len(farms),
                sale_count=sum(f.sale_count for f in farms),
                expense_count=sum(f.expense_count for f in farms),
                income_inr=income,
                expenses_inr=expenses,
                profit_inr=income - expenses,
                to_receive_inr=sum((f.to_receive_inr for f in farms), Decimal('0')),
                to_pay_inr=sum((f.to_pay_inr for f in farms), Decimal('0')),
                units_sold=sum((f.units_sold for f in farms), Decimal('0')),
            ),
            farms=farms,
            recent_sales=recent_sales,
        )

    async def export_unit(
        self,
        owner_user_id: uuid.UUID,
        unit_id: uuid.UUID,
        *,
        export_format: str,
        include_daily: bool,
    ) -> Response:
        unit = await self._require_unit(owner_user_id, unit_id)
        cached = await read_export_cache(owner_user_id, unit.id, export_format, include_daily)
        if cached is not None:
            body, filename = cached
            media = (
                'application/json; charset=utf-8'
                if export_format == 'json'
                else 'text/csv; charset=utf-8'
            )
            log.info(
                'mitra_unit_export_cache_hit',
                user_id=str(owner_user_id),
                unit_id=str(unit_id),
                format=export_format,
            )
            return Response(
                content=body,
                media_type=media,
                headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )
        billing = await self._repo.list_all_billing_for_export(unit.id)
        expenses = await self._repo.list_all_expenses_for_export(unit.id)
        daily: list = []
        if include_daily:
            daily, _ = await self._repo.list_daily_records(unit.id, limit=5000, offset=0)

        if export_format == 'json':
            body, filename = build_json_export(unit, billing=billing, expenses=expenses, daily=daily)
            media = 'application/json; charset=utf-8'
        elif export_format == 'csv':
            body, filename = build_csv_export(unit, billing=billing, expenses=expenses, daily=daily)
            media = 'text/csv; charset=utf-8'
        else:
            raise AppError('format must be csv or json', code='mitra_export_format_invalid')

        await write_export_cache(
            owner_user_id, unit.id, export_format, include_daily, body, filename
        )
        log.info(
            'mitra_unit_exported',
            user_id=str(owner_user_id),
            unit_id=str(unit_id),
            format=export_format,
            include_daily=include_daily,
        )
        return Response(
            content=body,
            media_type=media,
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    @staticmethod
    def _recompute_expense_balance(row: MitraExpense) -> None:
        if row.paid_inr > row.amount_inr:
            row.paid_inr = row.amount_inr
        row.balance_inr = (row.amount_inr - row.paid_inr).quantize(Decimal('0.01'))
