"""geo_offices: village / post office names per pincode

Revision ID: e8c1d4a03b62
Revises: d1e4c7a92b51
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e8c1d4a03b62'
down_revision: str | Sequence[str] | None = 'd1e4c7a92b51'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'geo_offices',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('pincode', sa.String(length=6), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('office_type', sa.String(length=20), nullable=True),
        sa.Column('taluk', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['pincode'], ['geo_pincodes.pincode'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_geo_offices')),
        sa.UniqueConstraint('pincode', 'name', name='uq_geo_offices_pincode_name'),
    )
    op.create_index('ix_geo_offices_pincode', 'geo_offices', ['pincode'])
    op.create_index('ix_geo_offices_taluk', 'geo_offices', ['taluk'])


def downgrade() -> None:
    op.drop_index('ix_geo_offices_taluk', table_name='geo_offices')
    op.drop_index('ix_geo_offices_pincode', table_name='geo_offices')
    op.drop_table('geo_offices')
