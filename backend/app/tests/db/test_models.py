from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

import app.db.models  # noqa: F401
from app.db.base import Base

EXPECTED_TABLES = {
    'roles',
    'permissions',
    'role_permissions',
    'users',
    'user_profiles',
    'oauth_accounts',
    'user_sessions',
    'audit_logs',
    'market_egg_zones',
    'market_poultry_daily',
    'market_egg_daily',
    'market_egg_month_summaries',
    'market_sync_runs',
    'geo_pincodes',
    'geo_offices',
    'mitra_billing_sales',
    'mitra_gst_rate_slabs',
    'mitra_unit_billing_settings',
    'mitra_expenses',
    'mitra_farm_units',
    'mitra_daily_records',
    'mitra_hatchery_batches',
}


def test_expected_tables_are_registered():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_relationships_configure_without_errors():
    configure_mappers()


def test_every_table_compiles_for_postgres():
    for table in Base.metadata.sorted_tables:
        sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert table.name in sql


def _index_names(table: str) -> set[str]:
    return {str(i.name) for i in Base.metadata.tables[table].indexes}


def test_email_is_claimed_even_after_soft_delete():
    """Changed on purpose: uq_users_email_active became uq_users_email_any.

    A deactivated account keeps its address so a stranger cannot take it over;
    restore is the only way back in. See migration b7f3c1d94a20.
    """
    constraints = {c.name for c in Base.metadata.tables['users'].constraints}
    assert 'uq_users_email_any' in constraints
    assert 'uq_users_email_active' not in _index_names('users')
    assert 'ck_users_active_not_deleted' in constraints


def test_only_one_active_session_per_user_is_enforced():
    assert 'uq_user_sessions_one_active' in _index_names('user_sessions')


def test_profile_has_phone_and_pincode_checks():
    names = {c.name for c in Base.metadata.tables['user_profiles'].constraints}
    assert 'ck_user_profiles_phone_format' in names
    assert 'ck_user_profiles_pincode_format' in names
