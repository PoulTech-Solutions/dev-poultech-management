from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.roles import Role
from app.db.models.users import User


class AdminUsersRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_roles(
        self, roles: tuple[str, ...], *, limit: int, offset: int
    ) -> tuple[int, list[User]]:
        filters = (User.deleted_at.is_(None), Role.name.in_(roles))
        count_stmt = select(func.count(User.id)).join(Role, User.role_id == Role.id).where(*filters)
        total = (await self.db.execute(count_stmt)).scalar_one()
        stmt = (
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(*filters)
            .options(selectinload(User.role), selectinload(User.profile))
            .order_by(Role.name, User.email)
            .offset(offset)
            .limit(limit)
        )
        rows = list((await self.db.execute(stmt)).unique().scalars().all())
        return int(total), rows

    async def get_live(self, user_id: uuid.UUID) -> User | None:
        stmt = (
            select(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .options(selectinload(User.role), selectinload(User.profile))
        )
        return (await self.db.execute(stmt)).unique().scalar_one_or_none()
