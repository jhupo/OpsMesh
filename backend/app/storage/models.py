from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_files"
    __table_args__ = (
        Index("ix_workspace_files_workspace_status", "workspace_id", "status"),
        Index("ix_workspace_files_workspace_checksum", "workspace_id", "checksum_sha256"),
        CheckConstraint(
            "sensitivity in ('public', 'internal', 'confidential', 'restricted')",
            name="sensitivity_valid",
        ),
        CheckConstraint(
            "runtime_access in ('allowed', 'denied')",
            name="runtime_access_valid",
        ),
        CheckConstraint(
            "sensitivity != 'restricted' or runtime_access = 'denied'",
            name="restricted_runtime_denied",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="application/octet-stream",
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    sensitivity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="internal",
    )
    runtime_access: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="allowed",
    )
    file_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )


class FileAccessEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "file_access_events"
    __table_args__ = (
        Index("ix_file_access_events_workspace_file", "workspace_id", "workspace_file_id"),
        Index("ix_file_access_events_workspace_user", "workspace_id", "user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
