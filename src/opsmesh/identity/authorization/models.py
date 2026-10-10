from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class SecuredResource(TimestampMixin, Base):
    __tablename__ = "secured_resources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="RESTRICT",
        ),
        Index("ix_secured_resources_owner", "workspace_id", "owner_user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    resource_kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(nullable=True)


class ResourceGrant(TimestampMixin, Base):
    __tablename__ = "resource_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "resource_kind", "resource_id"],
            [
                "secured_resources.workspace_id",
                "secured_resources.resource_kind",
                "secured_resources.resource_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "action IN ('read','invoke','update','delete','share','control','approve')",
            name="resource_action_valid",
        ),
        Index("ix_resource_grants_subject", "workspace_id", "user_id", "action", "resource_kind"),
    )

    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(20), primary_key=True)
