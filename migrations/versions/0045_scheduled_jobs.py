"""create workspace scheduled jobs

Revision ID: 0045_scheduled_jobs
Revises: 0044_notification_center
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_scheduled_jobs"
down_revision: str | None = "0044_notification_center"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_scheduled_jobs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("schedule_type", sa.String(length=32), nullable=False),
        sa.Column("schedule_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("routing", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_scheduled_jobs")),
    )
    op.create_index(
        "ix_workspace_scheduled_jobs_created_by",
        "workspace_scheduled_jobs",
        ["workspace_id", "created_by_user_id"],
    )
    op.create_index(
        "ix_workspace_scheduled_jobs_due",
        "workspace_scheduled_jobs",
        ["status", "next_run_at"],
    )
    op.create_index(
        "ix_workspace_scheduled_jobs_workspace_status",
        "workspace_scheduled_jobs",
        ["workspace_id", "status"],
    )
    op.create_table(
        "workspace_scheduled_job_events",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("queued_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message", sa.String(length=1000), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_job_id"],
            ["workspace_scheduled_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_scheduled_job_events")),
    )
    op.create_index(
        "ix_workspace_scheduled_job_events_job_created",
        "workspace_scheduled_job_events",
        ["scheduled_job_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_scheduled_job_events_workspace_created",
        "workspace_scheduled_job_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_scheduled_job_events_workspace_status",
        "workspace_scheduled_job_events",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_scheduled_job_events_workspace_status",
        table_name="workspace_scheduled_job_events",
    )
    op.drop_index(
        "ix_workspace_scheduled_job_events_workspace_created",
        table_name="workspace_scheduled_job_events",
    )
    op.drop_index(
        "ix_workspace_scheduled_job_events_job_created",
        table_name="workspace_scheduled_job_events",
    )
    op.drop_table("workspace_scheduled_job_events")
    op.drop_index(
        "ix_workspace_scheduled_jobs_workspace_status",
        table_name="workspace_scheduled_jobs",
    )
    op.drop_index("ix_workspace_scheduled_jobs_due", table_name="workspace_scheduled_jobs")
    op.drop_index(
        "ix_workspace_scheduled_jobs_created_by",
        table_name="workspace_scheduled_jobs",
    )
    op.drop_table("workspace_scheduled_jobs")
