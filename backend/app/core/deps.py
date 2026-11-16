"""Authentication and authorization dependencies.

The request path is deliberately two-tier: a Redis read for the happy path, and
Postgres only on a cache miss. Revocation stays correct because the cache entry is
keyed by session id, carries the password epoch, and is dropped on logout.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.core.store import read_session_cache, write_session_cache
from app.db.models.roles import Role
from app.db.models.sessions import UserSession
from app.db.models.users import User
from app.db.session import DbSession

# Registers the Swagger "Authorize" lock (HTTP Bearer). auto_error=False so we keep
# our own UnauthorizedError shape instead of FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False, bearerFormat='JWT', scheme_name='BearerAuth')


@dataclass(frozen=True, slots=True)
class CurrentUserData:
    """A snapshot, not an ORM object: nothing here can trigger a lazy load later."""

    user_id: uuid.UUID
    role: str
    is_active: bool
    is_email_verified: bool
    permission_codes: frozenset[str]
    session_id: uuid.UUID | None
    token_pcat: int
    pwd_epoch: str
    token_version: int = 0

    def has_role(self, *names: str) -> bool:
        return self.role in names

    def has_permission(self, *codes: str) -> bool:
        return any(code in self.permission_codes for code in codes)


def _as_epoch(value: datetime | None) -> str:
    if value is None:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return str(int(value.timestamp()))


def _bearer_token(request: Request, credentials: HTTPAuthorizationCredentials | None = None) -> str:
    if credentials is not None and credentials.scheme.lower() == 'bearer' and credentials.credentials:
        return credentials.credentials.strip()
    header = request.headers.get('authorization') or ''
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise UnauthorizedError('Missing bearer token', code='missing_token')
    return token.strip()


async def _load_from_db(
    db, user_id: uuid.UUID, session_id: uuid.UUID, token_pcat: int, token_version: int, pwd_epoch: str
) -> CurrentUserData:
    stmt: Select = (
        select(User)
        .options(
            selectinload(User.role).selectinload(Role.permissions),
            selectinload(User.profile),
        )
        .where(User.id == user_id)
    )
    user = (await db.execute(stmt)).unique().scalar_one_or_none()
    if user is None:
        raise UnauthorizedError('Account no longer exists', code='account_gone')
    if not user.is_active or user.deleted_at is not None:
        raise ForbiddenError('Account is deactivated', code='account_inactive')
    if _as_epoch(user.password_changed_at) != pwd_epoch:
        # A password change invalidates every token minted before it, cached or
        # not: the claim is compared with the row, so there is nothing to revoke.
        raise UnauthorizedError('Sign in again after changing your password', code='password_changed')
    if user.token_version != token_version:
        # An admin bumped the account (or rotated keys): every older token dies here.
        raise UnauthorizedError('Session is no longer valid, sign in again', code='token_revoked')

    permissions = tuple(sorted(p.code for p in user.role.permissions))
    pwd_epoch = _as_epoch(user.password_changed_at)
    await write_session_cache(
        session_id=str(session_id),
        user_id=str(user.id),
        role=user.role.name,
        is_active=user.is_active,
        is_email_verified=user.is_email_verified,
        permissions=permissions,
        pwd_epoch=pwd_epoch,
        token_version=user.token_version,
    )
    return CurrentUserData(
        user_id=user.id,
        role=user.role.name,
        is_active=user.is_active,
        is_email_verified=user.is_email_verified,
        permission_codes=frozenset(permissions),
        session_id=session_id,
        token_pcat=token_pcat,
        pwd_epoch=pwd_epoch,
    )


async def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> CurrentUserData:
    claims = decode_access_token(_bearer_token(request, credentials))
    user_id = uuid.UUID(str(claims['sub']))
    session_id = uuid.UUID(str(claims['sid']))
    token_pcat = int(claims.get('pcat') or 0)
    pwd_epoch = str(claims.get('pwd') or '')
    token_version = int(claims.get('ver') or 0)

    cached = await read_session_cache(
        session_id=str(session_id), pwd_epoch=pwd_epoch, token_version=token_version
    )
    if cached is not None:
        if cached.get('u') != str(user_id):
            raise UnauthorizedError('Invalid session', code='invalid_session')
        if cached.get('a') != '1':
            raise ForbiddenError('Account is deactivated', code='account_inactive')
        return CurrentUserData(
            user_id=user_id,
            role=str(cached.get('r', '')),
            is_active=True,
            is_email_verified=cached.get('v') == '1',
            permission_codes=frozenset(cached.get('x') or ()),
            session_id=session_id,
            token_pcat=token_pcat,
            pwd_epoch=pwd_epoch,
            token_version=token_version,
        )

    alive = await db.execute(
        select(UserSession.id).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )
    if alive.scalar_one_or_none() is None:
        raise UnauthorizedError('Session ended, sign in again', code='session_revoked')

    return await _load_from_db(db, user_id, session_id, token_pcat, token_version, pwd_epoch)


CurrentUser = Annotated[CurrentUserData, Depends(get_current_user)]


def require_roles(*roles: str):
    """Guard for the admin/staff routes that come later; users never match it."""
    allowed = frozenset(roles)

    async def dependency(user: CurrentUser) -> CurrentUserData:
        if user.role not in allowed:
            raise ForbiddenError('This action needs a different role', code='role_forbidden')
        return user

    return dependency


def require_permissions(*codes: str):
    """Permissions are seeded per role, so admin features need no new plumbing."""
    required = frozenset(codes)

    async def dependency(user: CurrentUser) -> CurrentUserData:
        if not required.intersection(user.permission_codes):
            raise ForbiddenError('Missing permission', code='permission_required')
        return user

    return dependency


def self_only(target_user_id: uuid.UUID, user: CurrentUser) -> uuid.UUID:
    """One rule for every /users/{id} style route: you may only touch yourself.

    Admin management routes get their own dependency later; they never come
    through this helper.
    """
    if target_user_id != user.user_id:
        raise ForbiddenError('You can only access your own account', code='not_owner')
    return target_user_id
