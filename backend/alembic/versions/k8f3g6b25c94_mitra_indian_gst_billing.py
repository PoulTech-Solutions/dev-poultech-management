"""mitra indian GST billing settings and tax columns

Revision ID: k8f3g6b25c94
Revises: j7e2f5a14b83
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'k8f3g6b25c94'
down_revision: str | Sequence[str] | None = 'j7e2f5a14b83'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'mitra_unit_billing_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('unit_id', sa.Uuid(), nullable=False),
        sa.Column('owner_user_id', sa.Uuid(), nullable=False),
        sa.Column('is_gst_registered', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('seller_gstin', sa.String(length=15), nullable=True),
        sa.Column('seller_legal_name', sa.String(length=200), nullable=True),
        sa.Column('seller_state_code', sa.String(length=2), nullable=True),
        sa.Column('default_place_of_supply_state', sa.String(length=2), nullable=True),
        sa.Column('invoice_series_prefix', sa.String(length=20), nullable=True),
        sa.Column('late_payment_interest_pct_annual', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
        sa.Column('default_gst_rate_pct', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            'late_payment_interest_pct_annual >= 0 AND late_payment_interest_pct_annual <= 100',
            name=op.f('ck_mitra_billing_settings_interest'),
        ),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_mitra_unit_billing_settings_owner_user_id_users'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['unit_id'], ['mitra_farm_units.id'], name=op.f('fk_mitra_unit_billing_settings_unit_id_mitra_farm_units'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mitra_unit_billing_settings')),
        sa.UniqueConstraint('unit_id', name=op.f('uq_mitra_unit_billing_settings_unit_id')),
    )
    op.create_index(op.f('ix_mitra_unit_billing_settings_owner_user_id'), 'mitra_unit_billing_settings', ['owner_user_id'], unique=False)

    op.create_table(
        'mitra_gst_rate_slabs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_user_id', sa.Uuid(), nullable=False),
        sa.Column('hsn_sac', sa.String(length=12), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('gst_rate_pct', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('gst_rate_pct >= 0 AND gst_rate_pct <= 100', name=op.f('ck_mitra_gst_slab_rate')),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_mitra_gst_rate_slabs_owner_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mitra_gst_rate_slabs')),
        sa.UniqueConstraint('owner_user_id', 'hsn_sac', name=op.f('uq_mitra_gst_slab_owner_hsn')),
    )
    op.create_index(op.f('ix_mitra_gst_rate_slabs_owner_user_id'), 'mitra_gst_rate_slabs', ['owner_user_id'], unique=False)

    op.add_column('mitra_billing_sales', sa.Column('invoice_kind', sa.String(length=20), server_default='receipt', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('hsn_sac', sa.String(length=12), nullable=True))
    op.add_column('mitra_billing_sales', sa.Column('buyer_gstin', sa.String(length=15), nullable=True))
    op.add_column('mitra_billing_sales', sa.Column('place_of_supply_state', sa.String(length=2), nullable=True))
    op.add_column('mitra_billing_sales', sa.Column('supply_type', sa.String(length=16), server_default='exempt', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('tax_inclusive', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('taxable_value_inr', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('gst_rate_pct', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('cgst_inr', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('sgst_inr', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('igst_inr', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False))
    op.add_column('mitra_billing_sales', sa.Column('total_tax_inr', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False))
    op.create_check_constraint(
        op.f('ck_mitra_billing_invoice_kind'),
        'mitra_billing_sales',
        "invoice_kind IN ('tax_invoice','bill_of_supply','receipt')",
    )
    op.create_check_constraint(
        op.f('ck_mitra_billing_supply_type'),
        'mitra_billing_sales',
        "supply_type IN ('intra_state','inter_state','exempt')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f('ck_mitra_billing_supply_type'), 'mitra_billing_sales', type_='check')
    op.drop_constraint(op.f('ck_mitra_billing_invoice_kind'), 'mitra_billing_sales', type_='check')
    for col in (
        'total_tax_inr',
        'igst_inr',
        'sgst_inr',
        'cgst_inr',
        'gst_rate_pct',
        'taxable_value_inr',
        'tax_inclusive',
        'supply_type',
        'place_of_supply_state',
        'buyer_gstin',
        'hsn_sac',
        'invoice_kind',
    ):
        op.drop_column('mitra_billing_sales', col)
    op.drop_index(op.f('ix_mitra_gst_rate_slabs_owner_user_id'), table_name='mitra_gst_rate_slabs')
    op.drop_table('mitra_gst_rate_slabs')
    op.drop_index(op.f('ix_mitra_unit_billing_settings_owner_user_id'), table_name='mitra_unit_billing_settings')
    op.drop_table('mitra_unit_billing_settings')