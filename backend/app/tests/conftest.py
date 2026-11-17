"""Test harness: real Postgres, fake Redis, real ASGI app.

Design notes that matter more than the fixtures themselves:
  * the DB is a real one because unique indexes and check constraints are part of
    what we are testing - no mocking them
  * Redis is fakeredis (Lua enabled) so the OTP scripts run unchanged
  * every test starts from a clean set of tables, so order never matters
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

# Set before anything imports app.core.config, whose get_settings() caches for the
# whole process. A developer can now run plain `pytest` with no exports: the suite
# pins itself to the test environment instead of inheriting whatever `.env` says.
os.environ.setdefault('APP_ENV', 'test')
os.environ.setdefault('REDIS_FAKE', '1')
# Compose injects host `.env` into the container; force-off so OTP resend tests
# are not blocked by cooldown / hourly caps from a developer RATE_LIMIT_ENABLED=true.
os.environ['RATE_LIMIT_ENABLED'] = 'false'
# OTP tests enqueue Celery eager in-process; console avoids SMTP/Brevo from host `.env`.
os.environ['EMAIL_BACKEND'] = 'console'

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_PASSWORD = 'Str0ng!Pass1'
TEST_PHONE = '9876543210'
_TABLES = (
    'market_egg_daily',
    'market_egg_month_summaries',
    'market_sync_runs',
    'market_egg_zones',
    'user_sessions',
    'oauth_accounts',
    'audit_logs',
    'user_profiles',
    'users',
    'role_permissions',
    'permissions',
    'roles',
)


def _create_database_if_missing(url: str) -> None:
    """Make the test database if it is not there yet.

    CI and a fresh clone then need only a running Postgres. The suite drops and
    rebuilds this schema on every run, which is exactly why it must never point at
    a database anyone else is using - see env_file_for() in app/core/config.py.
    """
    from sqlalchemy import make_url, text

    target = make_url(url)
    if not target.database:
        return

    async def _ensure() -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        # Pass a URL object — str(url) hides the password as "***" and auth fails.
        admin = create_async_engine(target.set(database='postgres'), isolation_level='AUTOCOMMIT')
        async with admin.connect() as conn:
            found = await conn.execute(
                text('SELECT 1 FROM pg_database WHERE datname = :name'), {'name': target.database}
            )
            if found.scalar() is None:
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
        await admin.dispose()

    try:
        asyncio.run(_ensure())
    except Exception as exc:
        pytest.exit(
            f'cannot provision the test database {target.database!r}: {type(exc).__name__}: {exc}\n'
            'Point TEST_DATABASE_URL (or .env.test) at a database this role may create.'
        )


@pytest.fixture(scope='session')
def test_engine():
    """A throwaway schema per run: create_all, never migrate, never shared state.

    Deliberately a *sync* fixture holding an async engine: a session-scoped async
    fixture binds to one event loop and every function-scoped test then errors
    with ScopeMismatch. NullPool keeps connections from crossing loops.
    """
    import app.db.models  # noqa: F401
    from app.core.config import get_settings
    from app.db.base import Base

    settings = get_settings()
    _create_database_if_missing(settings.database_url)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)

    async def _prepare() -> None:
        async with engine.begin() as conn:
            await conn.execute(text('DROP SCHEMA IF EXISTS public CASCADE'))
            await conn.execute(text('CREATE SCHEMA public'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_prepare())
    yield engine
    asyncio.run(engine.dispose())


@pytest_asyncio.fixture
async def db(test_engine) -> AsyncIterator[AsyncSession]:
    """Truncate then hand out one session; the app reads through the same engine."""
    import app.db.session as session_module

    session_module.engine = test_engine
    session_module.SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)
    async with test_engine.begin() as conn:
        await conn.execute(text(f'TRUNCATE {", ".join(_TABLES)} RESTART IDENTITY CASCADE'))
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', timeout=30.0) as c:
        yield c


@pytest_asyncio.fixture
async def seeded(db: AsyncSession) -> None:
    from scripts.seed_identity import PERMISSIONS, ROLES
    from sqlalchemy import select

    from app.db.models.roles import Permission, Role

    for name, description in ROLES.items():
        role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
        if role is None:
            db.add(Role(name=name, description=description, is_system=True))
    await db.flush()
    for code, (description, role_names) in PERMISSIONS.items():
        perm = (await db.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
        if perm is None:
            perm = Permission(code=code, description=description)
            db.add(perm)
            await db.flush()
        role_rows = (await db.execute(select(Role).where(Role.name.in_(role_names)))).scalars().all()
        for role in role_rows:
            if code not in {p.code for p in role.permissions}:
                role.permissions = [*role.permissions, perm]
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _redis_client_per_loop() -> AsyncIterator[None]:
    """Rebuild the Redis singleton for every test.

    pytest-asyncio gives each test its own event loop, while redis-py caches its
    connections; reusing a socket from a loop that has closed hangs the next read.
    fakeredis has no sockets to inherit, which is exactly why this only shows up
    under `REDIS_FAKE=0` - and why it is worth having here.
    """
    from app.core.redis_conn import close_redis

    try:
        yield
    finally:
        await close_redis()


# --------------------------------------------------------------------- helpers


async def read_otp(purpose: str, session_id: str) -> str:
    """The test stands in for the mailbox: it decrypts the sealed payload the
    worker would have received, so no OTP is ever hard-coded.
    """
    from app.core.redis_conn import get_redis
    from app.core.security import decrypt_otp_payload
    from app.core.store import otp_key

    raw = await get_redis().hget(otp_key(purpose, session_id), 'raw')
    assert raw, f'no sealed otp for {purpose}:{session_id}'
    return decrypt_otp_payload(str(raw), session_id=session_id, purpose=purpose)


def new_email(prefix: str = 'farmer') -> str:
    return f'{prefix}.{uuid.uuid4().hex[:10]}@example.com'


def new_phone() -> str:
    """Unique Indian mobile (10 digits, leading 6-9); signup enforces one phone per account."""
    return f'9{uuid.uuid4().int % 1_000_000_000:09d}'


async def register_and_verify(client: AsyncClient, *, email: str | None = None, **overrides):
    email = email or new_email()
    body = {
        'email': email,
        'password': TEST_PASSWORD,
        'confirm_password': TEST_PASSWORD,
        'first_name': 'Ashish',
        'last_name': 'Kumar',
        **overrides,
    }
    body.setdefault('phone', new_phone())
    signup = await client.post('/api/v1/auth/signup', json=body)
    assert signup.status_code == 202, signup.text
    sid = signup.json()['otp_session_id']
    otp = await read_otp('signup', sid)
    verified = await client.post(
        '/api/v1/auth/verify-email', json={'otp_session_id': sid, 'otp': otp, 'purpose': 'signup'}
    )
    assert verified.status_code == 200, verified.text
    client._poultech_email = email  # type: ignore[attr-defined]
    return {'email': email, 'password': TEST_PASSWORD}


async def login(client: AsyncClient, email: str, password: str = TEST_PASSWORD, *, confirm: bool = False):
    extra = {'confirm_device_switch': True} if confirm else {}
    step1 = await client.post('/api/v1/auth/login', json={'email': email, 'password': password, **extra})
    assert step1.status_code == 200, step1.text
    sid = step1.json()['otp_session_id']
    otp = await read_otp('login', sid)
    step2 = await client.post(
        '/api/v1/auth/verify-otp', json={'otp_session_id': sid, 'otp': otp, 'purpose': 'login'}
    )
    assert step2.status_code == 200, step2.text
    tokens = step2.json()
    # The refresh token only ever arrives as an httpOnly cookie. Read the header
    # directly: the shared jar would keep two same-named cookies (one per path).
    tokens['refresh_token'] = cookie_value(step2.headers)
    return tokens


def cookie_value(headers, name: str = 'poultech_refresh') -> str:
    """Read a Set-Cookie header straight off a response (no shared jar)."""
    pairs = headers if isinstance(headers, list) else headers.get_list('set-cookie')
    for header in pairs:
        if header.startswith(f'{name}='):
            return header.split(';', 1)[0].split('=', 1)[1]
    return ''


def _cookie_value(headers: list[str]) -> str:
    for header in headers:
        if header.startswith('poultech_refresh='):
            return header.split(';', 1)[0].split('=', 1)[1]
    return ''


async def relogin(client: AsyncClient, email: str, password: str = TEST_PASSWORD) -> dict:
    """A second sign-in on the same client: the device slot is already taken, so
    this is the path a real user takes after choosing 'move session here'.
    """
    return await login(client, email, password, confirm=True)


def bearer(tokens: dict) -> dict:
    return {'Authorization': f'Bearer {tokens["access_token"]}'}


def tokens_email(client: AsyncClient) -> str:
    """The address the last register_and_verify used for this client."""
    value = getattr(client, '_poultech_email', None)
    assert value, 'register_and_verify() must run first'
    return str(value)
