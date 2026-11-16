from sqlalchemy import Boolean, Column, ForeignKey, String, Table, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id', Uuid, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    Column('permission_id', Uuid, ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True),
)


class Permission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'permissions'

    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)  # e.g. user:read
    description: Mapped[str | None] = mapped_column(String(255))


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'roles'

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    is_system: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text('false'), nullable=False
    )

    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy='selectin')
