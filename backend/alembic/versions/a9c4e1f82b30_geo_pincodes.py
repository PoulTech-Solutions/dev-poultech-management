"""geo_pincodes reference table + dev seed rows

Revision ID: a9c4e1f82b30
Revises: e7f2b3c94d21
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a9c4e1f82b30'
down_revision: str | Sequence[str] | None = 'e7f2b3c94d21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED = [
    ('110001', 'Delhi', 'New Delhi', 'Connaught Place'),
    ('400001', 'Maharashtra', 'Mumbai', 'Mumbai'),
    ('411001', 'Maharashtra', 'Pune', 'Pune'),
    ('411002', 'Maharashtra', 'Pune', 'Pune'),
    ('560001', 'Karnataka', 'Bengaluru Urban', 'Bengaluru'),
    ('600001', 'Tamil Nadu', 'Chennai', 'Chennai'),
    ('500001', 'Telangana', 'Hyderabad', 'Hyderabad'),
    ('700001', 'West Bengal', 'Kolkata', 'Kolkata'),
    ('302001', 'Rajasthan', 'Jaipur', 'Jaipur'),
    ('380001', 'Gujarat', 'Ahmedabad', 'Ahmedabad'),
    ('226001', 'Uttar Pradesh', 'Lucknow', 'Lucknow'),
    ('452001', 'Madhya Pradesh', 'Indore', 'Indore'),
]


def upgrade() -> None:
    op.create_table(
        'geo_pincodes',
        sa.Column('pincode', sa.String(length=6), nullable=False),
        sa.Column('state', sa.String(length=100), nullable=False),
        sa.Column('district', sa.String(length=100), nullable=False),
        sa.Column('tehsil', sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint('pincode', name=op.f('pk_geo_pincodes')),
    )

    seed = sa.table(
        'geo_pincodes',
        sa.column('pincode', sa.String),
        sa.column('state', sa.String),
        sa.column('district', sa.String),
        sa.column('tehsil', sa.String),
    )
    op.bulk_insert(
        seed,
        [
            {'pincode': p, 'state': s, 'district': d, 'tehsil': t}
            for p, s, d, t in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_table('geo_pincodes')