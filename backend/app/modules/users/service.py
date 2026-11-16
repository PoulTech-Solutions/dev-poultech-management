"""Own-profile operations: read, update, change email, sessions, delete.

One rule dominates this file: the acting user id and the target user id are the
same number, always. There is no admin path through here - by design, nobody but
the owner can read, change or delete a profile in this build.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import Request
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUserData
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import verify_password_async
from app.core.store import drop_session_cache, read_profile_cache, warm_session_caches
from app.core.user_snapshot import permission_codes, profile_snapshot
from app.db.models.users import User
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import AuthError, Issued, mask_email
from app.modules.users.repository import UsersRepository
from app.modules.users.schemas import (
    AccountDeleteRequest,
    EmailChangeConfirmRequest,
    EmailChangeStartRequest,
    ProfileResponse,
    ProfileUpdateRequest,
)

log = structlog.get_logger()


class UsersService:
    def __init__(self, repo: UsersRepository, auth: AuthRepository) -> None:
        self._repo = repo
        self._auth = auth
        self._db = repo.db

    # ---------------------------------------------------------------- read

    async def payload(self, current: CurrentUserData) -> ProfileResponse:
        return await self.read_self(await self.require_user(current.user_id))

    async def require_user(self, user_id: uuid.UUID) -> User:
        user = await self._repo.get_self(user_id)
        if user is None:  # pragma: no cover - the auth dependency already checked
            raise UnauthorizedError('Account no longer exists', code='account_gone')
        return user

    async def read_self(self, user: User) -> ProfileResponse:
        return ProfileResponse.model_validate(profile_snapshot(user))

    # -------------------------------------------------------------- update

    async def update_profile(
        self, user: User, payload: ProfileUpdateRequest, *, session_id: uuid.UUID | None = None
    ) -> ProfileResponse:
        values = payload.model_dump(exclude_unset=True, exclude_none=True)
        profile = await self._repo.get_profile(user.id)
        phone = values.pop('phone', None)

        if phone and (profile is None or profile.phone_e164 != phone):
            owner = await self._repo.phone_owner(phone)
            if owner is not None and owner != user.id:
                raise ConflictError('This mobile number is already in use', code='phone_taken')

        if profile is None:
            # A Google sign-up has a profile, but an older row might not: make one.
            profile = await self._auth.upsert_profile(
                user_id=user.id,
                first_name=values.pop('first_name', 'Guest'),
                last_name=values.pop('last_name', '-'),
                phone_e164=phone,
            )
            values.pop('first_name', None)
            values.pop('last_name', None)
        if phone:
            values['phone_e164'] = phone
            # Verification is a separate feature; storing never claims it happened.
            values['is_phone_verified'] = False

        try:
            await self._repo.update_profile(profile, values)
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            if 'phone_e164' in str(exc.orig):
                raise ConflictError('This mobile number is already in use', code='phone_taken') from None
            raise
        fresh = await self._reload(user.id)
        log.info('profile_updated', user_id=str(user.id), fields=sorted(values))
        response = await self.read_self(fresh)
        if session_id is not None:
            await self._refresh_caches(fresh, session_id=session_id)
        return response

    async def profile_payload(self, current: CurrentUserData) -> dict:
        """Flat dict for the SPA: Redis first, Postgres on miss, then re-warm."""
        if current.session_id is not None:
            cached = await read_profile_cache(
                session_id=str(current.session_id), user_id=str(current.user_id)
            )
            if cached is not None:
                return cached
        user = await self.require_user(current.user_id)
        data = (await self.read_self(user)).model_dump()
        if current.session_id is not None:
            await self._refresh_caches(user, session_id=current.session_id)
        return data

    # --------------------------------------------------------- email change

    async def start_email_change(self, user: User, payload: EmailChangeStartRequest, auth_service) -> dict:
        """Verify-then-apply: nothing is written until the new address returns a code."""
        if payload.email == user.email:
            raise ConflictError('That is already your email address', code='email_unchanged')
        if user.password_hash is not None and not await verify_password_async(
            payload.password, user.password_hash
        ):
            raise UnauthorizedError('Password is incorrect', code='invalid_credentials')
        if await self._repo.email_taken(payload.email, exclude_user_id=user.id):
            raise ConflictError('Email is already registered', code='email_taken')
        challenge = await auth_service.issue_email_change_challenge(user=user, new_email=payload.email)
        await self._db.commit()
        return auth_service.challenge_payload(challenge)

    async def email_change_started(
        self, current: CurrentUserData, payload: EmailChangeStartRequest, auth_service
    ) -> dict:
        user = await self.require_user(current.user_id)
        return await self.start_email_change(user, payload, auth_service)

    async def confirm_email_change(
        self,
        current: CurrentUserData,
        payload: EmailChangeConfirmRequest,
        auth_service,
        request: Request,
    ) -> tuple[Issued, ProfileResponse]:
        """Apply the new email, kill every old session/token, mint a fresh pair.

        Login identity moved: old refresh hashes are revoked, session cache is
        dropped, ``token_version`` bumps so leftover access JWTs fail on sight,
        then this device gets a brand-new refresh cookie + access token.
        """
        state = await auth_service.read_challenge(sid=payload.otp_session_id, purpose='email_change')
        user_id = uuid.UUID(state['u'])
        new_email = state.get('p')
        if not new_email:
            raise AuthError('otp_expired', 'Start the email change again.', status=410)
        if user_id != current.user_id:
            # The code was issued to somebody else: never cross the wires.
            raise UnauthorizedError('This code is not for your account', code='not_owner')
        await auth_service.consume_email_change(
            sid=payload.otp_session_id, otp=payload.otp, user_id=user_id, identity=state.get('id', '')
        )
        user = await self._repo.get_self(user_id)
        if user is None:  # pragma: no cover
            raise UnauthorizedError('Account no longer exists', code='account_gone')

        open_ids = [s.id for s in await self._repo.open_sessions(user.id)]
        if current.session_id is not None and current.session_id not in open_ids:
            open_ids.append(current.session_id)

        try:
            await self._repo.set_email(user, new_email)
            await self._auth.revoke_all_sessions(user.id, reason='email_change')
            await self._db.commit()
        except IntegrityError:
            await self._db.rollback()
            raise ConflictError('Email is already registered', code='email_taken') from None

        for sid in open_ids:
            await drop_session_cache(session_id=str(sid))

        user = await self._reload(user.id)
        # confirm=True: we just revoked every row, including this device.
        issued = await auth_service._open_session(user, request, confirm=True, purpose='email_change')
        profile = await self.read_self(user)
        log.info('email_changed', user_id=str(user.id), destination=mask_email(new_email))
        return issued, profile

    # ------------------------------------------------------------ sessions

    async def sessions_payload(self, current: CurrentUserData) -> dict:
        user = await self.require_user(current.user_id)
        return {
            'items': await self.list_sessions(user, current_session_id=current.session_id),
            'one_device_at_a_time': True,
        }

    async def list_sessions(self, user: User, *, current_session_id: uuid.UUID | None) -> list[dict]:
        sessions = await self._repo.open_sessions(user.id)
        return [
            {
                'session_id': str(s.id),
                'created_at': s.created_at.isoformat(),
                'last_used_at': s.last_used_at.isoformat(),
                'expires_at': s.expires_at.isoformat(),
                'user_agent': s.user_agent,
                'ip_address': s.ip_address,
                'current': s.id == current_session_id,
            }
            for s in sessions
        ]

    async def revoke_session_by_id(self, current: CurrentUserData, session_id: uuid.UUID) -> None:
        user = await self.require_user(current.user_id)
        await self.revoke_session(user, session_id, current_session_id=current.session_id)

    async def revoke_session(
        self, user: User, session_id: uuid.UUID, *, current_session_id: uuid.UUID | None
    ) -> None:
        session = await self._repo.get_session(user.id, session_id)
        if session is None or session.revoked_at is not None:
            raise ConflictError('That session is already closed', code='session_gone')
        if session_id == current_session_id:
            raise ConflictError('Use sign out to close the device you are on', code='own_session')
        await self._repo.revoke(session, 'admin' if user.role.name == 'admin' else 'logout')
        await self._db.commit()
        await drop_session_cache(session_id=str(session_id))
        log.info('session_revoked', user_id=str(user.id), session_id=str(session_id))

    # ------------------------------------------------------------ deletion

    async def delete_self(self, current: CurrentUserData, payload: AccountDeleteRequest) -> str:
        user = await self.require_user(current.user_id)
        return await self.delete_account(user, payload)

    async def delete_account(self, user: User, payload: AccountDeleteRequest) -> str:
        """Soft delete: the row survives, access stops, the email stays claimed."""
        if user.password_hash is None or not await verify_password_async(
            payload.password, user.password_hash
        ):
            raise UnauthorizedError('Password is incorrect', code='invalid_credentials')
        sessions = await self._repo.open_sessions(user.id)
        await self._auth.soft_delete_user(user)
        await self._db.commit()
        for session in sessions:
            await drop_session_cache(session_id=str(session.id))
        log.warning('account_deleted', user_id=str(user.id))
        return (
            'Your account is deactivated. Nothing was deleted: sign in again, or use '
            f'"Reactivate account" with {mask_email(user.email)}, to come back.'
        )

    # ------------------------------------------------------------- helpers

    async def _reload(self, user_id: uuid.UUID) -> User:
        user = await self._repo.get_self(user_id)
        if user is None:  # pragma: no cover
            raise UnauthorizedError('Account no longer exists', code='account_gone')
        return user

    async def _refresh_caches(self, user: User, *, session_id: uuid.UUID) -> None:
        pwd = user.password_changed_at
        if pwd is not None and pwd.tzinfo is None:
            from datetime import UTC

            pwd = pwd.replace(tzinfo=UTC)
        epoch = '' if pwd is None else str(int(pwd.timestamp()))
        await warm_session_caches(
            session_id=str(session_id),
            user_id=str(user.id),
            role=user.role.name,
            is_active=user.is_active,
            is_email_verified=user.is_email_verified,
            permissions=permission_codes(user),
            pwd_epoch=epoch,
            token_version=user.token_version,
            profile=profile_snapshot(user),
        )
