"""Workflow drafts, append-only revisions, grants and addressed invitations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'public')", name="workflow_visibility"),
        CheckConstraint("status IN ('active', 'archived')", name="workflow_status"),
        CheckConstraint("revision > 0 AND published_number >= 0", name="workflow_revision"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_number: Mapped[int] = mapped_column(Integer, default=0)
    draft: Mapped[dict[str, object]] = mapped_column(JSONB)


class WorkflowRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_revisions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "number"),
        CheckConstraint("number > 0", name="workflow_release_number"),
        CheckConstraint("visibility IN ('private', 'public')", name="workflow_release_visibility"),
    )

    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_definitions.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    visibility: Mapped[str] = mapped_column(String(16))
    digest: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict[str, object]] = mapped_column(JSONB)
    published_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class WorkflowGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_grants"
    __table_args__ = (UniqueConstraint("workflow_id", "user_id"),)

    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_definitions.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    permissions: Mapped[list[str]] = mapped_column(JSONB)


class WorkflowInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked')",
            name="workflow_invitation_status",
        ),
        Index(
            "uq_workflow_pending_invitation",
            "workflow_id",
            "recipient_user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_definitions.id"), index=True)
    invited_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    recipient_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    permissions: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
