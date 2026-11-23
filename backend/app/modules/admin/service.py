from __future__ import annotations

import uuid

import structlog
from sqlalchemy.exc import IntegrityError

from app.core.constants import RoleName
from app.core.deps import CurrentUserData
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password_async
from app.core.store import drop_session_cache
from app.core.user_snapshot import profile_snapshot
from app.db.models.users import User
from app.modules.admin.repository import AdminUsersRepository
from app.modules.admin.schemas import (
    AdminConfirmUserRequest,
    AdminCreateUserRequest,
    AdminUserItem,
    AdminUserListResponse,
)
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import AuthError, AuthService

log = structlog.get_logger()

_FARMER = frozenset({RoleName.USER, RoleName.FARM_OWNER, RoleName.FARM_MEMBER})
_CREATABLE = {
    RoleName.SUPER_ADMIN: frozenset({RoleName.ADMIN, RoleName.STAFF}),
    RoleName.ADMIN: frozenset({RoleName.STAFF}),
}
_MANAGEABLE = {
    RoleName.SUPER_ADMIN: _CREATABLE[RoleName.SUPER_ADMIN] | _FARMER,
    RoleName.ADMIN: _CREATABLE[RoleName.ADMIN] | _FARMER,
}
_DIRECTORY = {
    RoleName.SUPER_ADMIN: _MANAGEABLE[RoleName.SUPER_ADMIN],
    RoleName.ADMIN: _MANAGEABLE[RoleName.ADMIN],
    RoleName.STAFF: _FARMER,
}


def _opt(snap: dict, key: str) -> str | None:
    value = snap.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _item(user: User) -> AdminUserItem:
    snap = profile_snapshot(user)
    return AdminUserItem(
        user_id=str(user.id),
        email=user.email,
        role=user.role.name,
        first_name=str(snap['first_name']),
        last_name=str(snap['last_name']),
        phone=_opt(snap, 'phone'),
        address_line1=_opt(snap, 'address_line1'),
        village_city=_opt(snap, 'village_city'),
        tehsil=_opt(snap, 'tehsil'),
        district=_opt(snap, 'district'),
        state=_opt(snap, 'state'),
        pincode=_opt(snap, 'pincode'),
        is_active=user.is_active,
        is_email_verified=user.is_email_verified,
        created_at=str(snap['created_at']),
        last_login_at=_opt(snap, 'last_login_at'),
    )


class AdminUsersService:
    def __init__(self, repo: AdminUsersRepository, auth: AuthRepository) -> None:
        self._repo = repo
        self._auth = auth
        self._db = repo.db

    def _creatable(self, actor: CurrentUserData) -> frozenset[str]:
        return _CREATABLE.get(actor.role, frozenset())

    def _manageable(self, actor: CurrentUserData) -> frozenset[str]:
        return _MANAGEABLE.get(actor.role, frozenset())

    def _listable(self, actor: CurrentUserData) -> frozenset[str]:
        return _DIRECTORY.get(actor.role, frozenset())

    def _require_create_role(self, actor: CurrentUserData, role: str) -> None:
        if role not in self._creatable(actor):
            raise ForbiddenError('You cannot create that role', code='role_forbidden')

    def _require_manage_role(self, actor: CurrentUserData, role: str) -> None:
        if role not in self._manageable(actor):
            raise ForbiddenError('You cannot manage that role', code='role_forbidden')

    async def list_users(
        self, actor: CurrentUserData, *, limit: int, offset: int
    ) -> AdminUserListResponse:
        allowed = tuple(self._listable(actor))
        if not allowed:
            raise ForbiddenError('You cannot list accounts here', code='permission_required')
        total, rows = await self._repo.list_by_roles(allowed, limit=limit, offset=offset)
        items = [_item(row) for row in rows]
        return AdminUserListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )

    async def start_create(self, actor: CurrentUserData, payload: AdminCreateUserRequest) -> dict:
        """Email OTP first. The account is inserted only after confirm_create."""
        self._require_create_role(actor, payload.role)
        if await self._auth.email_taken(payload.email):
            raise ConflictError('Email is already registered', code='email_taken')
        if await self._auth.phone_taken(payload.phone):
            raise ConflictError('This mobile number is already in use', code='phone_taken')
        role = await self._auth.get_role_by_name(payload.role)
        if role is None:
            raise ConflictError('Role is not seeded', code='role_missing')
        extra = {
            'fn': payload.first_name,
            'ln': payload.last_name,
            'ph': payload.phone,
            'pw': await hash_password_async(payload.password),
            'rl': payload.role,
            'ac': str(actor.user_id),
        }
        auth = AuthService(self._auth)
        challenge = await auth.issue_internal_signup(
            email=payload.email, extra=extra, display_name=payload.first_name
        )
        log.info('internal_signup_otp_pending', actor=str(actor.user_id), role=payload.role)
        return auth.challenge_payload(challenge)

    async def confirm_create(
        self, actor: CurrentUserData, payload: AdminConfirmUserRequest
    ) -> AdminUserItem:
        auth = AuthService(self._auth)
        preview = await auth.read_challenge(sid=payload.otp_session_id, purpose='internal_signup')
        if preview.get('ac') != str(actor.user_id):
            raise ForbiddenError('This code was issued for another admin session', code='not_owner')
        role_name = preview.get('rl', '')
        self._require_create_role(actor, role_name)
        try:
            state = await auth.consume_internal_signup(payload)
        except AuthError:
            raise
        first_name = state.get('fn', '').strip()
        last_name = state.get('ln', '').strip()
        phone = state.get('ph', '').strip()
        password_hash = state.get('pw', '')
        email = state.get('id', '').strip()
        if not (first_name and last_name and phone and password_hash and email):
            raise AuthError('signup_expired', 'Start this registration again.', status=410)
        if await self._auth.email_taken(email):
            raise ConflictError('Email is already registered', code='email_taken')
        if await self._auth.phone_taken(phone):
            raise ConflictError('This mobile number is already in use', code='phone_taken')
        role = await self._auth.get_role_by_name(role_name)
        if role is None:
            raise ConflictError('Role is not seeded', code='role_missing')
        try:
            user = await self._auth.create_user(
                email=email,
                password_hash=password_hash,
                role=role,
                is_active=True,
                is_email_verified=True,
            )
            await self._auth.upsert_profile(
                user_id=user.id,
                first_name=first_name,
                last_name=last_name,
                phone_e164=phone,
            )
            await self._auth.mark_profile_complete(user.id)
            await self._db.commit()
        except IntegrityError:
            await self._db.rollback()
            raise ConflictError('Email is already registered', code='email_taken') from None
        created = await self._repo.get_live(user.id)
        assert created is not None
        log.info(
            'internal_user_created',
            actor=str(actor.user_id),
            role=role_name,
            user_id=str(user.id),
            email_verified=True,
        )
        return _item(created)

    async def set_active(self, actor: CurrentUserData, user_id: uuid.UUID, *, active: bool) -> AdminUserItem:
        target = await self._load_target(actor, user_id)
        sessions = await self._auth.list_sessions(target.id, limit=50)
        reason = 'admin_deactivate' if not active else 'admin_reactivate'
        await self._auth.set_active(target, active=active, reason=reason)
        await self._db.commit()
        if not active:
            for session in sessions:
                await drop_session_cache(session_id=str(session.id))
        fresh = await self._repo.get_live(user_id)
        assert fresh is not None
        log.info('internal_user_active', actor=str(actor.user_id), user_id=str(user_id), active=active)
        return _item(fresh)

    async def delete_user(self, actor: CurrentUserData, user_id: uuid.UUID) -> None:
        target = await self._load_target(actor, user_id)
        sessions = await self._auth.list_sessions(target.id, limit=50)
        await self._auth.soft_delete_user(target)
        await self._db.commit()
        for session in sessions:
            await drop_session_cache(session_id=str(session.id))
        log.warning('internal_user_deleted', actor=str(actor.user_id), user_id=str(user_id))

    async def _load_target(self, actor: CurrentUserData, user_id: uuid.UUID) -> User:
        if user_id == actor.user_id:
            raise ForbiddenError('Use your own profile tools for this account', code='self_forbidden')
        target = await self._repo.get_live(user_id)
        if target is None:
            raise NotFoundError('Account not found')
        self._require_manage_role(actor, target.role.name)
        return target
