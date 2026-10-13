from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SandboxReference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A product reference to SDK state; lifecycle remains owned by the SDK backend."""

    __tablename__ = "sandbox_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workspace_runtime_id"],
            ["workspace_runtimes.workspace_id", "workspace_runtimes.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "owner_kind", "owner_id", name="uq_sandbox_reference_owner"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_sandbox_reference_scope"),
        CheckConstraint(
            "owner_kind IN ('run', 'environment', 'mcp', 'command', 'plugin')",
            name="sandbox_owner_kind",
        ),
        CheckConstraint(
            "status IN ('provisioning', 'running', 'stopped', 'deleting', 'deleted', 'unknown')",
            name="sandbox_reference_status",
        ),
        Index("ix_sandbox_references_runtime", "workspace_id", "workspace_runtime_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    workspace_runtime_id: Mapped[UUID] = mapped_column(nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column(nullable=True)
    backend_id: Mapped[str] = mapped_column(String(64), nullable=False)
    native_session_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="provisioning")
    encrypted_session_state: Mapped[str | None] = mapped_column(Text)
    encryption_key_id: Mapped[str | None] = mapped_column(String(80))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
