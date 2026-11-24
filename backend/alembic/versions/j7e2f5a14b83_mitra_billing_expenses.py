"""mitra billing sales and expenses

Revision ID: j7e2f5a14b83
Revises: h5c0d3e92f61
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'j7e2f5a14b83'
down_revision: str | Sequence[str] | None = 'h5c0d3e92f61'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'mitra_billing_sales',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('unit_id', sa.Uuid(), nullable=False),
        sa.Column('invoice_no', sa.String(length=64), nullable=False),
        sa.Column('sale_date', sa.Date(), nullable=False),
        sa.Column('flock_batch_ref', sa.String(length=64), nullable=True),
        sa.Column('buyer', sa.String(length=200), nullable=False),
        sa.Column('item', sa.String(length=120), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column('qty_unit', sa.String(length=32), nullable=False),
        sa.Column('rate_inr', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('amount_inr', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('pay_mode', sa.String(length=32), nullable=True),
        sa.Column('received_inr', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('balance_inr', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('amount_inr >= 0', name=op.f('ck_mitra_billing_amount_nonneg')),
        sa.CheckConstraint('balance_inr >= 0', name=op.f('ck_mitra_billing_balance_nonneg')),
        sa.CheckConstraint('received_inr >= 0', name=op.f('ck_mitra_billing_received_nonneg')),
        sa.ForeignKeyConstraint(
            ['unit_id'], ['mitra_farm_units.id'], name=op.f('fk_mitra_billing_sales_unit_id_mitra_farm_units'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mitra_billing_sales')),
        sa.UniqueConstraint('unit_id', 'invoice_no', name=op.f('uq_mitra_billing_invoice')),
    )
    op.create_index(op.f('ix_mitra_billing_sales_unit_id'), 'mitra_billing_sales', ['unit_id'], unique=False)
    op.create_index('ix_mitra_billing_sales_unit_date', 'mitra_billing_sales', ['unit_id', 'sale_date'], unique=False)

    op.create_table(
        'mitra_expenses',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('unit_id', sa.Uuid(), nullable=False),
        sa.Column('expense_date', sa.Date(), nullable=False),
        sa.Column('bill_no', sa.String(length=64), nullable=True),
        sa.Column('flock_batch_ref', sa.String(length=64), nullable=True),
        sa.Column('supplier', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=80), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False),
        sa.Column('amount_inr', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('paid_inr', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('balance_inr', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('amount_inr > 0', name=op.f('ck_mitra_expense_amount_pos')),
        sa.CheckConstraint('balance_inr >= 0', name=op.f('ck_mitra_expense_balance_nonneg')),
        sa.CheckConstraint('paid_inr >= 0', name=op.f('ck_mitra_expense_paid_nonneg')),
        sa.ForeignKeyConstraint(
            ['unit_id'], ['mitra_farm_units.id'], name=op.f('fk_mitra_expenses_unit_id_mitra_farm_units'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mitra_expenses')),
        sa.UniqueConstraint('unit_id', 'bill_no', name=op.f('uq_mitra_expense_bill')),
    )
    op.create_index(op.f('ix_mitra_expenses_unit_id'), 'mitra_expenses', ['unit_id'], unique=False)
    op.create_index('ix_mitra_expenses_unit_date', 'mitra_expenses', ['unit_id', 'expense_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_mitra_expenses_unit_date', table_name='mitra_expenses')
    op.drop_index(op.f('ix_mitra_expenses_unit_id'), table_name='mitra_expenses')
    op.drop_table('mitra_expenses')
    op.drop_index('ix_mitra_billing_sales_unit_date', table_name='mitra_billing_sales')
    op.drop_index(op.f('ix_mitra_billing_sales_unit_id'), table_name='mitra_billing_sales')
    op.drop_table('mitra_billing_sales')