"""Persist parent/child execution boundaries for subworkflow nodes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0076_subworkflow_invocations"
down_revision = "0075_workflow_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subworkflow_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_task_step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("task_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "child_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orchestration_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("output_payload", postgresql.JSONB(), nullable=True),
        sa.Column("error_payload", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="subworkflow_invocation_status_valid",
        ),
        sa.UniqueConstraint("parent_run_id", name="uq_subworkflow_invocations_parent_run"),
        sa.UniqueConstraint("child_task_id", name="uq_subworkflow_invocations_child_task"),
    )
    op.create_index(
        "ix_subworkflow_invocations_workspace_status",
        "subworkflow_invocations",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_subworkflow_invocations_workspace_parent_task",
        "subworkflow_invocations",
        ["workspace_id", "parent_task_id"],
    )
    op.create_index(
        "ix_subworkflow_invocations_workspace_child_task",
        "subworkflow_invocations",
        ["workspace_id", "child_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subworkflow_invocations_workspace_child_task",
        table_name="subworkflow_invocations",
    )
    op.drop_index(
        "ix_subworkflow_invocations_workspace_parent_task",
        table_name="subworkflow_invocations",
    )
    op.drop_index("ix_subworkflow_invocations_workspace_status", table_name="subworkflow_invocations")
    op.drop_table("subworkflow_invocations")
