"""market egg rates (NECC zones, daily prices, sync runs)

Revision ID: c4e8a91b2f10
Revises: b7f3c1d94a20
Create Date: 2026-11-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e8a91b2f10'
down_revision: str | Sequence[str] | None = 'b7f3c1d94a20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'market_egg_zones',
        sa.Column('necc_name', sa.String(length=120), nullable=False),
        sa.Column('lookup_key', sa.String(length=120), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_market_egg_zones')),
        sa.UniqueConstraint('lookup_key', name=op.f('uq_market_egg_zones_lookup_key')),
        sa.UniqueConstraint('necc_name', name=op.f('uq_market_egg_zones_necc_name')),
    )
    op.create_index(op.f('ix_market_egg_zones_lookup_key'), 'market_egg_zones', ['lookup_key'], unique=False)

    op.create_table(
        'market_egg_daily',
        sa.Column('zone_id', sa.Uuid(), nullable=False),
        sa.Column('rate_date', sa.Date(), nullable=False),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('sheet_year', sa.Integer(), nullable=False),
        sa.Column('sheet_month', sa.Integer(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['zone_id'], ['market_egg_zones.id'], name=op.f('fk_market_egg_daily_zone_id_market_egg_zones'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_market_egg_daily')),
        sa.UniqueConstraint('zone_id', 'rate_date', name='uq_market_egg_daily_zone_date'),
    )
    op.create_index(op.f('ix_market_egg_daily_zone_id'), 'market_egg_daily', ['zone_id'], unique=False)

    op.create_table(
        'market_sync_runs',
        sa.Column('sheet_year', sa.Integer(), nullable=False),
        sa.Column('sheet_month', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('rows_upserted', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_market_sync_runs')),
    )

    op.add_column('user_profiles', sa.Column('market_preferred_zone', sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column('user_profiles', 'market_preferred_zone')
    op.drop_table('market_sync_runs')
    op.drop_index(op.f('ix_market_egg_daily_zone_id'), table_name='market_egg_daily')
    op.drop_table('market_egg_daily')
    op.drop_index(op.f('ix_market_egg_zones_lookup_key'), table_name='market_egg_zones')
    op.drop_table('market_egg_zones')