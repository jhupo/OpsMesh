from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DomainProject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "domain_projects"
    __table_args__ = (
        Index("ix_domain_projects_workspace_domain", "workspace_id", "domain_type"),
        Index("ix_domain_projects_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    domain_type: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)


class DomainItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "domain_items"
    __table_args__ = (
        Index("ix_domain_items_workspace_project", "workspace_id", "domain_project_id"),
        Index("ix_domain_items_workspace_task", "workspace_id", "task_id"),
        Index("ix_domain_items_workspace_type_status", "workspace_id", "item_type", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("domain_projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("domain_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    project: Mapped[DomainProject | None] = relationship()


class ReviewComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        Index("ix_review_comments_workspace_task", "workspace_id", "task_id"),
        Index("ix_review_comments_workspace_item", "workspace_id", "domain_item_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("domain_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    body: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )


class RevisionRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "revision_requests"
    __table_args__ = (
        Index("ix_revision_requests_workspace_task", "workspace_id", "task_id"),
        Index("ix_revision_requests_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("domain_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    instruction: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
