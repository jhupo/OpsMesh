"""create domain task extensions

Revision ID: 0007_domain_tasks
Revises: 0006_create_approvals
Create Date: 2026-05-17 15:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_domain_tasks"
down_revision: str | None = "0006_create_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_projects",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("domain_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_team_id"], ["agent_teams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_projects")),
    )
    op.create_index("ix_domain_projects_workspace_domain", "domain_projects", ["workspace_id", "domain_type"])
    op.create_index("ix_domain_projects_workspace_status", "domain_projects", ["workspace_id", "status"])

    op.create_table(
        "domain_items",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["domain_project_id"], ["domain_projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_item_id"], ["domain_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_items")),
    )
    op.create_index("ix_domain_items_workspace_project", "domain_items", ["workspace_id", "domain_project_id"])
    op.create_index("ix_domain_items_workspace_task", "domain_items", ["workspace_id", "task_id"])
    op.create_index("ix_domain_items_workspace_type_status", "domain_items", ["workspace_id", "item_type", "status"])

    op.create_table(
        "review_comments",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["author_agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["domain_item_id"], ["domain_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_comments")),
    )
    op.create_index("ix_review_comments_workspace_item", "review_comments", ["workspace_id", "domain_item_id"])
    op.create_index("ix_review_comments_workspace_task", "review_comments", ["workspace_id", "task_id"])

    op.create_table(
        "revision_requests",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instruction", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assigned_agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["domain_item_id"], ["domain_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revision_requests")),
    )
    op.create_index("ix_revision_requests_workspace_status", "revision_requests", ["workspace_id", "status"])
    op.create_index("ix_revision_requests_workspace_task", "revision_requests", ["workspace_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_revision_requests_workspace_task", table_name="revision_requests")
    op.drop_index("ix_revision_requests_workspace_status", table_name="revision_requests")
    op.drop_table("revision_requests")
    op.drop_index("ix_review_comments_workspace_task", table_name="review_comments")
    op.drop_index("ix_review_comments_workspace_item", table_name="review_comments")
    op.drop_table("review_comments")
    op.drop_index("ix_domain_items_workspace_type_status", table_name="domain_items")
    op.drop_index("ix_domain_items_workspace_task", table_name="domain_items")
    op.drop_index("ix_domain_items_workspace_project", table_name="domain_items")
    op.drop_table("domain_items")
    op.drop_index("ix_domain_projects_workspace_status", table_name="domain_projects")
    op.drop_index("ix_domain_projects_workspace_domain", table_name="domain_projects")
    op.drop_table("domain_projects")
