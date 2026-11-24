"""mitra: index daily records by unit + date (list / range queries)

Revision ID: h5c0d3e92f61
Revises: g4b9c2d81e50
Create Date: 2026-11-24

"""

from collections.abc import Sequence

from alembic import op

revision: str = 'h5c0d3e92f61'
down_revision: str | Sequence[str] | None = 'g4b9c2d81e50'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        'ix_mitra_daily_records_unit_date',
        'mitra_daily_records',
        ['unit_id', 'record_date'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_mitra_daily_records_unit_date', table_name='mitra_daily_records')