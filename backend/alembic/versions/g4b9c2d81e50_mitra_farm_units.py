"""mitra farm units, daily records, hatchery batches

Revision ID: g4b9c2d81e50
Revises: e8c1d4a03b62
Create Date: 2026-11-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'g4b9c2d81e50'
down_revision: str | Sequence[str] | None = 'e8c1d4a03b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FARM_TYPES = (
    "'layer','broiler','pullet','breeder','hatchery',"
    "'kadaknath','duck_layer','duck_meat','quail','turkey','desi'"
)


def upgrade() -> None:
    op.create_table(
        'mitra_farm_units',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_user_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('farm_type', sa.String(length=32), nullable=False),
        sa.Column('contact_phone', sa.String(length=16), nullable=False),
        sa.Column('state', sa.String(length=100), nullable=False),
        sa.Column('district', sa.String(length=100), nullable=False),
        sa.Column('tehsil', sa.String(length=100), nullable=True),
        sa.Column('village_city', sa.String(length=100), nullable=True),
        sa.Column('pincode', sa.String(length=6), nullable=True),
        sa.Column('address_line1', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('hatch_date', sa.Date(), nullable=True),
        sa.Column('birds_placed', sa.Integer(), nullable=True),
        sa.Column('proprietor', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"farm_type IN ({_FARM_TYPES})", name=op.f('ck_mitra_farm_units_type')),
        sa.CheckConstraint("contact_phone ~ '^[+]91[6-9][0-9]{9}$'", name=op.f('ck_mitra_farm_units_phone')),
        sa.CheckConstraint("pincode IS NULL OR pincode ~ '^[1-9][0-9]{5}$'", name=op.f('ck_mitra_farm_units_pincode')),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_mitra_farm_units_owner_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mitra_farm_units')),
    )
    op.create_index(op.f('ix_mitra_farm_units_owner_user_id'), 'mitra_farm_units', ['owner_user_id'], unique=False)
    op.create_index(op.f('ix_mitra_farm_units_deleted_at'), 'mitra_farm_units', ['deleted_at'], unique=False)
    op.create_index(
        'ix_mitra_farm_units_owner_active',
        'mitra_farm_units',
        ['owner_user_id'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )

    op.create_table(
        'mitra_daily_records',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('unit_id', sa.Uuid(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['unit_id'], ['mitra_farm_units.id'], name=op.f('fk_mitra_daily_records_unit_id_mitra_farm_units'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mitra_daily_records')),
        sa.UniqueConstraint('unit_id', 'record_date', name=op.f('uq_mitra_daily_unit_date')),
    )
    op.create_index(op.f('ix_mitra_daily_records_unit_id'), 'mitra_daily_records', ['unit_id'], unique=False)

    op.create_table(
        'mitra_hatchery_batches',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('unit_id', sa.Uuid(), nullable=False),
        sa.Column('batch_code', sa.String(length=64), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['unit_id'], ['mitra_farm_units.id'], name=op.f('fk_mitra_hatchery_batches_unit_id_mitra_farm_units'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_mitra_hatchery_batches')),
        sa.UniqueConstraint('unit_id', 'batch_code', name=op.f('uq_mitra_hatchery_batch_code')),
    )
    op.create_index(op.f('ix_mitra_hatchery_batches_unit_id'), 'mitra_hatchery_batches', ['unit_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mitra_hatchery_batches_unit_id'), table_name='mitra_hatchery_batches')
    op.drop_table('mitra_hatchery_batches')
    op.drop_index(op.f('ix_mitra_daily_records_unit_id'), table_name='mitra_daily_records')
    op.drop_table('mitra_daily_records')
    op.drop_index('ix_mitra_farm_units_owner_active', table_name='mitra_farm_units')
    op.drop_index(op.f('ix_mitra_farm_units_deleted_at'), table_name='mitra_farm_units')
    op.drop_index(op.f('ix_mitra_farm_units_owner_user_id'), table_name='mitra_farm_units')
    op.drop_table('mitra_farm_units')