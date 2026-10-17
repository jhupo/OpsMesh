"""Project: one workspace's unit of assembled configuration and execution.

The project carries the same basic state isolation as its workspace: an
archived project refuses writes while staying readable, a deleted one
refuses every access. Members, invitations and the assembled configuration
live in the sibling modules; this table is only the project fact.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

PROJECT_ACTIVE = "active"
PROJECT_ARCHIVED = "archived"
PROJECT_DELETED = "deleted"
PROJECT_STATUSES = (PROJECT_ACTIVE, PROJECT_ARCHIVED, PROJECT_DELETED)

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One project inside one workspace."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_project_workspace"),
        CheckConstraint("status IN ('active', 'archived', 'deleted')", name="project_status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, default="", nullable=False, server_default=""
    )
    status: Mapped[str] = mapped_column(
        String(16), default=PROJECT_ACTIVE, nullable=False, index=True
    )
