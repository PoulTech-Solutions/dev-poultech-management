"""Allow multiple billing rows per invoice number (line items).

Revision ID: m9a1b2c34d56
Revises: k8f3g6b25c94
Create Date: 2026-11-24

"""

from collections.abc import Sequence

from alembic import op

revision: str = 'm9a1b2c34d56'
down_revision: str | Sequence[str] | None = 'k8f3g6b25c94'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint('uq_mitra_billing_invoice', 'mitra_billing_sales', type_='unique')
    op.create_index(
        'ix_mitra_billing_sales_unit_invoice',
        'mitra_billing_sales',
        ['unit_id', 'invoice_no'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_mitra_billing_sales_unit_invoice', table_name='mitra_billing_sales')
    op.create_unique_constraint(
        'uq_mitra_billing_invoice',
        'mitra_billing_sales',
        ['unit_id', 'invoice_no'],
    )
