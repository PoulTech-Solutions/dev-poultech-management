from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.core.exceptions import AppError
from app.modules.admin.schemas import (
    AdminDashboardFarmCard,
    AdminDashboardResponse,
    AdminDashboardSaleItem,
    AdminDashboardTotals,
)
from app.modules.mitra.repository import MitraRepository


class AdminDashboardService:
    def __init__(self, repo: MitraRepository) -> None:
        self._repo = repo

    async def dashboard(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        sales_limit: int = 50,
    ) -> AdminDashboardResponse:
        self._validate_date_range(from_date, to_date)
        farm_rows = await self._repo.admin_dashboard_by_unit(from_date=from_date, to_date=to_date)
        sales_by_unit = await self._repo.admin_sales_by_unit(
            from_date=from_date, to_date=to_date, sales_limit=sales_limit
        )

        farms: list[AdminDashboardFarmCard] = []
        for row in farm_rows:
            unit_id: uuid.UUID = row['unit_id']
            sales = [
                AdminDashboardSaleItem(
                    id=sale.id,
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
                for sale in sales_by_unit.get(unit_id, [])
            ]
            farms.append(
                AdminDashboardFarmCard(
                    unit_id=unit_id,
                    unit_name=row['unit_name'],
                    farm_type=row['farm_type'],
                    state=row['state'],
                    district=row['district'],
                    owner_user_id=row['owner_user_id'],
                    owner_email=row['owner_email'],
                    income_inr=row['income_inr'],
                    expenses_inr=row['expenses_inr'],
                    profit_inr=row['profit_inr'],
                    to_receive_inr=row['to_receive_inr'],
                    to_pay_inr=row['to_pay_inr'],
                    units_sold=row['units_sold'],
                    sale_count=row['sale_count'],
                    expense_count=row['expense_count'],
                    sales=sales,
                )
            )

        income = sum((f.income_inr for f in farms), Decimal('0'))
        expenses = sum((f.expenses_inr for f in farms), Decimal('0'))
        owners = {f.owner_user_id for f in farms}
        return AdminDashboardResponse(
            from_date=from_date,
            to_date=to_date,
            totals=AdminDashboardTotals(
                farm_count=len(farms),
                owner_count=len(owners),
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
        )

    @staticmethod
    def _validate_date_range(from_date: date | None, to_date: date | None) -> None:
        if from_date is not None and to_date is not None and from_date > to_date:
            raise AppError('from_date must not be after to_date', code='mitra_invalid_date_range')
