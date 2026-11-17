"""market month summary + profile tehsil

Revision ID: e7f2b3c94d21
Revises: c4e8a91b2f10
Create Date: 2026-11-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e7f2b3c94d21'
down_revision: str | Sequence[str] | None = 'c4e8a91b2f10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'market_egg_month_summaries',
        sa.Column('zone_id', sa.Uuid(), nullable=False),
        sa.Column('sheet_year', sa.Integer(), nullable=False),
        sa.Column('sheet_month', sa.Integer(), nullable=False),
        sa.Column('necc_sheet_average', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('computed_daily_average', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('days_with_price', sa.Integer(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['zone_id'], ['market_egg_zones.id'], name=op.f('fk_market_egg_month_summaries_zone_id_market_egg_zones'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_market_egg_month_summaries')),
        sa.UniqueConstraint('zone_id', 'sheet_year', 'sheet_month', name='uq_market_egg_month_zone_period'),
    )
    op.create_index(
        op.f('ix_market_egg_month_summaries_zone_id'), 'market_egg_month_summaries', ['zone_id'], unique=False
    )
    op.add_column('user_profiles', sa.Column('tehsil', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('user_profiles', 'tehsil')
    op.drop_index(op.f('ix_market_egg_month_summaries_zone_id'), table_name='market_egg_month_summaries')
    op.drop_table('market_egg_month_summaries')