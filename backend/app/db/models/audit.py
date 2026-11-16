import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Append-only record of who did what. Rows are never updated or deleted."""

    __tablename__ = 'audit_logs'

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey('users.id', ondelete='SET NULL'), index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. user.login
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONB)  # never put secrets here

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index('ix_audit_logs_action_created_at', 'action', 'created_at'),
        # "everything that happened to this record" - the query the admin panel
        # will run on day one, so the index belongs with the table.
        Index('ix_audit_logs_target', 'target_type', 'target_id', 'created_at'),
    )
