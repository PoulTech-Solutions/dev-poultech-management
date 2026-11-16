from __future__ import annotations

import uuid
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.profile import UserProfile
from app.db.models.roles import Role
from app.db.models.sessions import UserSession
from app.db.models.users import User


class UsersRepository:
    """Profile reads/writes. Tenant rule: every query here is keyed by the
    caller's own user id - the router proves that id before calling.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_self(self, user_id: uuid.UUID) -> User | None:
        stmt = (
            select(User)
            .options(selectinload(User.role).selectinload(Role.permissions), selectinload(User.profile))
            .where(User.id == user_id, User.deleted_at.is_(None))
        )
        return (await self.db.execute(stmt)).unique().scalar_one_or_none()

    async def get_profile(self, user_id: uuid.UUID) -> UserProfile | None:
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def update_profile(self, profile: UserProfile, values: dict) -> UserProfile:
        for key, value in values.items():
            setattr(profile, key, value)
        profile.is_profile_complete = profile.has_required_fields
        await self.db.flush()
        return profile

    async def phone_owner(self, phone_e164: str) -> uuid.UUID | None:
        stmt = (
            select(UserProfile.user_id)
            .join(User, User.id == UserProfile.user_id)
            .where(UserProfile.phone_e164 == phone_e164, User.deleted_at.is_(None))
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def email_taken(self, email: str, *, exclude_user_id: uuid.UUID) -> bool:
        stmt = select(User.id).where(User.email == email, User.id != exclude_user_id).limit(1)
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def set_email(self, user: User, email: str) -> None:
        user.email = email.lower()
        # A new address is an unproven address until its own OTP says otherwise.
        user.is_email_verified = False
        # Bump so every access token minted under the old identity dies immediately
        # (deps compares `ver` / token_version without needing a denylist).
        user.token_version = int(user.token_version or 0) + 1
        await self.db.flush()

    async def mark_email_verified(self, user: User) -> None:
        user.is_email_verified = True
        profile = await self.get_profile(user.id)
        if profile is not None:
            profile.is_profile_complete = profile.has_required_fields
        await self.db.flush()

    async def open_sessions(self, user_id: uuid.UUID) -> list[UserSession]:
        stmt = (
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .order_by(UserSession.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def revoke(self, session: UserSession, reason: str) -> None:
        from datetime import datetime

        session.revoked_at = datetime.now(UTC)
        session.revoked_reason = reason[:50]
        await self.db.flush()

    async def soft_delete(self, user: User) -> None:
        from datetime import datetime

        user.is_active = False
        user.deleted_at = datetime.now(UTC)
        await self.db.flush()
