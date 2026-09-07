from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, UUIDPrimaryKeyMixin


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_workspace_action", "workspace_id", "action"),
        Index("ix_audit_events_workspace_target", "workspace_id", "target_type", "target_id"),
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_events_workspace_current_hash", "workspace_id", "current_hash"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False)
    audit_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    previous_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    current_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


def _reject_audit_event_update(mapper: object, connection: object, target: AuditEvent) -> None:
    raise ValueError("Audit events are append-only and cannot be updated")


def _reject_audit_event_delete(mapper: object, connection: object, target: AuditEvent) -> None:
    if getattr(target, "_audit_retention_delete_allowed", False) is True:
        return
    raise ValueError("Audit events are WORM protected and cannot be deleted")


event.listen(AuditEvent, "before_update", _reject_audit_event_update)
event.listen(AuditEvent, "before_delete", _reject_audit_event_delete)


class AuditIntegrityCheck(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_integrity_checks"
    __table_args__ = (
        Index(
            "ix_audit_integrity_checks_workspace_created",
            "workspace_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    checked_events: Mapped[int] = mapped_column(nullable=False)
    valid: Mapped[bool] = mapped_column(nullable=False)
    broken_event_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
