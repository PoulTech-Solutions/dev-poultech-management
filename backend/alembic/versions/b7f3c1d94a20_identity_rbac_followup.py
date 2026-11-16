"""identity follow-up: email claim, token version, restore, audit target index

Second revision on purpose: 845d0362cbee may already be applied on someone's
machine, and applied migrations are never edited. Everything here is additive or
swap-in, so deploys can run it against a live schema.

Revision ID: b7f3c1d94a20
Revises: 845d0362cbee
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7f3c1d94a20'
down_revision: str | Sequence[str] | None = '845d0362cbee'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. A deactivated account still claims its email: restore is the only way back,
    #    and that keeps "one person, one login" true for both email and phone.
    op.drop_index('uq_users_email_active', table_name='users', postgresql_where=sa.text('deleted_at IS NULL'))
    # A constraint (not a bare index): unique=True here and UniqueConstraint on the
    # model must describe the same object, or every later autogenerate shows drift.
    op.create_unique_constraint('uq_users_email_any', 'users', ['email'])

    # 2. Bumped to end every issued token at once (admin deactivation, key rotation).
    op.add_column(
        'users',
        sa.Column(
            'token_version', sa.Integer(), server_default=sa.text('0'), nullable=False,
            comment='Increments to invalidate all access tokens for this account',
        ),
    )
    # 3. Backs POST /auth/restore with a single-use hash; the plaintext never lands here.
    op.add_column('users', sa.Column('restore_token_hash', sa.String(length=64), nullable=True))
    op.create_index(
        'ix_users_restore_token_hash', 'users', ['restore_token_hash'], unique=False
    )
    op.create_check_constraint(
        'ck_users_active_not_deleted', 'users', 'deleted_at IS NULL OR is_active = false'
    )
    # Restore lookup by address, only over rows that are actually deleted.
    op.create_index(
        'ix_users_email_deleted',
        'users',
        ['email'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NOT NULL'),
    )

    # 4. The admin panel's first query, so the index ships with the table change.
    op.create_index(
        'ix_audit_logs_target', 'audit_logs', ['target_type', 'target_id', 'created_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_audit_logs_target', table_name='audit_logs')
    op.drop_index(
        'ix_users_email_deleted', table_name='users', postgresql_where=sa.text('deleted_at IS NOT NULL')
    )
    op.drop_constraint('ck_users_active_not_deleted', 'users', type_='check')
    op.drop_index('ix_users_restore_token_hash', table_name='users')
    op.drop_column('users', 'restore_token_hash')
    op.drop_column('users', 'token_version')
    op.drop_constraint('uq_users_email_any', 'users', type_='unique')
    op.create_index(
        'uq_users_email_active', 'users', ['email'], unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
