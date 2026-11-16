"""All SQL for the auth module. Services never build queries.

Only `deleted_at IS NULL` rows are "live"; a soft-deleted account is reachable
exclusively through get_by_email_any / get_including_deleted, because restore is
the only way back in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.core.config import get_settings
from app.db.models.oauth import OAuthAccount
from app.db.models.profile import UserProfile
from app.db.models.roles import Role
from app.db.models.sessions import UserSession
from app.db.models.users import User

LoginState = Literal['ok', 'unknown', 'google_only', 'password_wrong', 'deleted', 'locked']


def _live_user_stmt() -> Select:
    return select(User).where(User.deleted_at.is_(None))


def _user_options(stmt: Select) -> Select:
    return stmt.options(selectinload(User.role).selectinload(Role.permissions), selectinload(User.profile))


class AuthRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------------------------------------------------------- roles

    async def get_role_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ---------------------------------------------------------------- users

    async def get_by_email(self, email: str) -> User | None:
        stmt = _user_options(_live_user_stmt().where(User.email == email))
        return (await self.db.execute(stmt)).unique().scalar_one_or_none()

    async def get_by_email_any(self, email: str) -> User | None:
        """Includes soft-deleted rows: needed by login and the restore flow."""
        stmt = _user_options(select(User).where(User.email == email))
        return (await self.db.execute(stmt)).unique().scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = _user_options(_live_user_stmt().where(User.id == user_id))
        return (await self.db.execute(stmt)).unique().scalar_one_or_none()

    async def get_including_deleted(self, user_id: uuid.UUID) -> User | None:
        stmt = _user_options(select(User).where(User.id == user_id))
        return (await self.db.execute(stmt)).unique().scalar_one_or_none()

    async def email_taken(self, email: str) -> bool:
        stmt = select(User.id).where(User.email == email).limit(1)
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def phone_taken(self, phone_e164: str) -> bool:
        stmt = select(UserProfile.user_id).where(UserProfile.phone_e164 == phone_e164).limit(1)
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def create_user(self, *, email: str, password_hash: str | None, role: Role) -> User:
        user = User(email=email, password_hash=password_hash, role_id=role.id)
        self.db.add(user)
        await self.db.flush()
        return user

    async def touch_login(self, user: User, *, success: bool, locked_until: datetime | None = None) -> None:
        if success:
            user.failed_login_count = 0
            user.locked_until = None
            user.last_login_at = datetime.now(UTC)
        else:
            user.failed_login_count += 1
            user.locked_until = locked_until
        await self.db.flush()

    async def set_password(
        self,
        user: User,
        *,
        password_hash: str,
        keep_session_id: uuid.UUID | None,
        reason: str,
    ) -> int:
        now = datetime.now(UTC)
        user.password_hash = password_hash
        user.password_changed_at = now
        user.failed_login_count = 0
        user.locked_until = None
        revoked = await self.revoke_all_sessions(user.id, keep_session_id=keep_session_id, reason=reason)
        if keep_session_id is not None:
            # The kept device stays usable: last_used_at is what the refresh check
            # compares against, so stamp it as part of the same change.
            kept = await self.db.get(UserSession, keep_session_id)
            if kept is not None:
                kept.last_used_at = now
        return revoked

    async def verify_email(self, user: User) -> None:
        user.is_email_verified = True
        await self.db.flush()

    async def change_email(self, user: User, email: str) -> None:
        """Only ever called after the new address has proved it received the OTP."""
        user.email = email.lower()
        await self.db.flush()

    # ------------------------------------------------------------- profiles

    async def upsert_profile(
        self, *, user_id: uuid.UUID, first_name: str, last_name: str, phone_e164: str | None
    ) -> UserProfile:
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        profile = (await self.db.execute(stmt)).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(
                user_id=user_id, first_name=first_name, last_name=last_name, phone_e164=phone_e164
            )
            self.db.add(profile)
        else:
            profile.first_name = first_name
            profile.last_name = last_name
            if phone_e164:
                profile.phone_e164 = phone_e164
        await self.db.flush()
        return profile

    async def mark_profile_complete(self, user_id: uuid.UUID) -> None:
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        profile = (await self.db.execute(stmt)).scalar_one_or_none()
        if profile is not None:
            profile.is_profile_complete = profile.has_required_fields
            await self.db.flush()

    # ------------------------------------------------------------- sessions

    async def active_session(self, user_id: uuid.UUID) -> UserSession | None:
        stmt = select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_session(self, session_id: uuid.UUID) -> UserSession | None:
        return await self.db.get(UserSession, session_id)

    async def get_session_by_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_session_by_previous_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.previous_refresh_token_hash == token_hash)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_sessions(self, user_id: uuid.UUID, *, limit: int = 20) -> list[UserSession]:
        stmt = (
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .order_by(UserSession.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def create_session(
        self,
        *,
        user_id: uuid.UUID,
        refresh_token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
    ) -> UserSession:
        session = UserSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            user_agent=(user_agent or '')[:255] or None,
            ip_address=(ip_address or '')[:64] or None,
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def rotate_session(
        self, session: UserSession, *, refresh_token_hash: str, expires_at: datetime
    ) -> None:
        session.previous_refresh_token_hash = session.refresh_token_hash
        session.refresh_token_hash = refresh_token_hash
        session.expires_at = expires_at
        session.last_used_at = datetime.now(UTC)
        await self.db.flush()

    async def revoke_session(self, session: UserSession, *, reason: str) -> None:
        session.revoked_at = datetime.now(UTC)
        session.revoked_reason = reason[:50]
        await self.db.flush()

    async def revoke_all_sessions(
        self,
        user_id: uuid.UUID,
        *,
        keep_session_id: uuid.UUID | None = None,
        reason: str = 'admin',
    ) -> int:
        stmt = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revoked_reason=reason[:50])
        )
        if keep_session_id is not None:
            stmt = stmt.where(UserSession.id != keep_session_id)
        result = await self.db.execute(stmt)
        # UPDATE/DELETE through ORM puts the count on the cursor; ORM-only results
        # have none, which is exactly the 0 case.
        return int(getattr(result, 'rowcount', 0) or 0)

    async def soft_delete_user(self, user: User) -> None:
        user.is_active = False
        user.deleted_at = datetime.now(UTC)
        user.restore_token_hash = None
        await self.revoke_all_sessions(user.id, reason='account_deleted')
        await self.db.flush()

    async def restore_user(self, user: User) -> None:
        user.is_active = True
        user.deleted_at = None
        user.failed_login_count = 0
        user.locked_until = None
        user.restore_token_hash = None
        await self.db.flush()

    # ---------------------------------------------------------------- google

    async def find_google_account(self, provider_user_id: str) -> OAuthAccount | None:
        stmt = select(OAuthAccount).where(
            OAuthAccount.provider == 'google', OAuthAccount.provider_user_id == provider_user_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def link_google_account(self, *, user: User, provider_user_id: str, email: str) -> None:
        if await self.find_google_account(provider_user_id) is not None:
            return
        self.db.add(
            OAuthAccount(
                user_id=user.id,
                provider='google',
                provider_user_id=provider_user_id,
                email=email,
            )
        )
        await self.db.flush()

    async def apply_google_profile_hints(
        self,
        user_id: uuid.UUID,
        *,
        first_name: str,
        last_name: str,
        avatar_url: str | None,
    ) -> None:
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        profile = (await self.db.execute(stmt)).scalar_one_or_none()
        if profile is None:
            self.db.add(
                UserProfile(
                    user_id=user_id,
                    first_name=first_name,
                    last_name=last_name,
                    avatar_url=avatar_url,
                )
            )
        else:
            if not profile.first_name.strip():
                profile.first_name = first_name
            if not profile.last_name.strip():
                profile.last_name = last_name
            if avatar_url and not profile.avatar_url:
                profile.avatar_url = avatar_url
        await self.db.flush()
        await self.mark_profile_complete(user_id)

    async def create_google_user(
        self, *, email: str, first_name: str, last_name: str, avatar_url: str | None
    ) -> User:
        role_name = get_settings().default_role_name
        role = await self.get_role_by_name(role_name)
        if role is None:
            raise RuntimeError(f'roles not seeded, run scripts/seed_roles.py (missing {role_name!r})')
        user = User(
            email=email,
            password_hash=None,
            role_id=role.id,
            # Google proved ownership of this address, so the email OTP is redundant.
            is_email_verified=True,
        )
        self.db.add(user)
        await self.db.flush()
        self.db.add(
            UserProfile(
                user_id=user.id,
                first_name=first_name,
                last_name=last_name,
                avatar_url=avatar_url,
            )
        )
        await self.db.flush()
        return user
