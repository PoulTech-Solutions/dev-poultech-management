"""market poultry manual rates (culls, broilers)

Revision ID: f3a8b2c91d40
Revises: a9c4e1f82b30
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f3a8b2c91d40'
down_revision: str | Sequence[str] | None = 'a9c4e1f82b30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'market_poultry_daily',
        sa.Column('zone_id', sa.Uuid(), nullable=False),
        sa.Column('product', sa.String(length=20), nullable=False),
        sa.Column('rate_date', sa.Date(), nullable=False),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('source', sa.String(length=20), server_default='manual', nullable=False),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("product IN ('culls', 'broilers')", name=op.f('ck_market_poultry_product')),
        sa.CheckConstraint("source IN ('manual', 'api')", name=op.f('ck_market_poultry_source')),
        sa.ForeignKeyConstraint(
            ['zone_id'],
            ['market_egg_zones.id'],
            name=op.f('fk_market_poultry_daily_zone_id_market_egg_zones'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_market_poultry_daily')),
        sa.UniqueConstraint('zone_id', 'product', 'rate_date', name=op.f('uq_market_poultry_daily')),
    )
    op.create_index(op.f('ix_market_poultry_daily_zone_id'), 'market_poultry_daily', ['zone_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_market_poultry_daily_zone_id'), table_name='market_poultry_daily')
    op.drop_table('market_poultry_daily')