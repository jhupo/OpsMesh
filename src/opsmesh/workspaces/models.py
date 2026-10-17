"""Workspace: the tenant root every space-scoped fact hangs off.

One workspace carries members, projects, credentials, capacity accounts,
model connections and notifications. Its status is the basic state
isolation the step requires: an archived workspace refuses writes while
keeping its facts readable, and a deleted one is gone for every purpose
without erasing history.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

WORKSPACE_ACTIVE = "active"
WORKSPACE_ARCHIVED = "archived"
WORKSPACE_DELETED = "deleted"
WORKSPACE_STATUSES = (WORKSPACE_ACTIVE, WORKSPACE_ARCHIVED, WORKSPACE_DELETED)

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One tenant; the owner membership is created with it in one transaction."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived', 'deleted')", name="workspace_status"
        ),
    )

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, default="", nullable=False, server_default=""
    )
    status: Mapped[str] = mapped_column(
        String(16), default=WORKSPACE_ACTIVE, nullable=False, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
