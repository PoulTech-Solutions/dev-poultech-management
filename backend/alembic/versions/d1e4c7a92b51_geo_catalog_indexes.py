"""geo_pincodes state/district indexes + extra hub rows

Revision ID: d1e4c7a92b51
Revises: f3a8b2c91d40
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd1e4c7a92b51'
down_revision: str | Sequence[str] | None = 'f3a8b2c91d40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXTRA = [
    ('400050', 'Maharashtra', 'Mumbai', 'Bandra'),
    ('400601', 'Maharashtra', 'Thane', 'Thane'),
    ('410206', 'Maharashtra', 'Raigad', 'Panvel'),
    ('411014', 'Maharashtra', 'Pune', 'Pune'),
    ('411028', 'Maharashtra', 'Pune', 'Pune'),
    ('411045', 'Maharashtra', 'Pune', 'Pune'),
    ('412105', 'Maharashtra', 'Pune', 'Haveli'),
    ('413001', 'Maharashtra', 'Solapur', 'Solapur'),
    ('415001', 'Maharashtra', 'Satara', 'Satara'),
    ('416001', 'Maharashtra', 'Kolhapur', 'Kolhapur'),
    ('422001', 'Maharashtra', 'Nashik', 'Nashik'),
    ('431001', 'Maharashtra', 'Chhatrapati Sambhajinagar', 'Chhatrapati Sambhajinagar'),
    ('440001', 'Maharashtra', 'Nagpur', 'Nagpur'),
    ('444601', 'Maharashtra', 'Amravati', 'Amravati'),
    ('421301', 'Maharashtra', 'Thane', 'Kalyan'),
    ('110006', 'Delhi', 'Central Delhi', 'Chandni Chowk'),
    ('122001', 'Haryana', 'Gurugram', 'Gurugram'),
    ('201301', 'Uttar Pradesh', 'Gautam Buddha Nagar', 'Noida'),
    ('302004', 'Rajasthan', 'Jaipur', 'Jaipur'),
    ('380006', 'Gujarat', 'Ahmedabad', 'Ahmedabad'),
    ('395003', 'Gujarat', 'Surat', 'Surat'),
    ('500003', 'Telangana', 'Hyderabad', 'Hyderabad'),
    ('560034', 'Karnataka', 'Bengaluru Urban', 'Bengaluru'),
    ('600002', 'Tamil Nadu', 'Chennai', 'Chennai'),
    ('641001', 'Tamil Nadu', 'Coimbatore', 'Coimbatore'),
    ('682001', 'Kerala', 'Ernakulam', 'Kochi'),
    ('751001', 'Odisha', 'Khordha', 'Bhubaneswar'),
    ('781001', 'Assam', 'Kamrup Metropolitan', 'Guwahati'),
    ('800001', 'Bihar', 'Patna', 'Patna'),
    ('141001', 'Punjab', 'Ludhiana', 'Ludhiana'),
]


def upgrade() -> None:
    op.create_index('ix_geo_pincodes_state', 'geo_pincodes', ['state'])
    op.create_index('ix_geo_pincodes_state_district', 'geo_pincodes', ['state', 'district'])

    bind = op.get_bind()
    for pincode, state, district, tehsil in _EXTRA:
        bind.execute(
            sa.text(
                """
                INSERT INTO geo_pincodes (pincode, state, district, tehsil)
                VALUES (:pincode, :state, :district, :tehsil)
                ON CONFLICT (pincode) DO NOTHING
                """
            ),
            {'pincode': pincode, 'state': state, 'district': district, 'tehsil': tehsil},
        )


def downgrade() -> None:
    op.drop_index('ix_geo_pincodes_state_district', table_name='geo_pincodes')
    op.drop_index('ix_geo_pincodes_state', table_name='geo_pincodes')
