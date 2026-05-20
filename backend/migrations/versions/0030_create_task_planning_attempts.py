"""create task planning attempts

Revision ID: 0030_task_planning_attempts
Revises: 0029_add_model_provider_health
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_task_planning_attempts"
down_revision: str | None = "0029_add_model_provider_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_planning_attempts",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("planner_agent_profile_id", sa.UUID(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("strategy", sa.String(length=120), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["planner_agent_profile_id"],
            ["agent_profiles.id"],
            name=op.f("fk_task_planning_attempts_planner_agent_profile_id_agent_profiles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_planning_attempts_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_task_planning_attempts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_planning_attempts")),
    )
    op.create_index(
        "ix_task_planning_attempts_workspace_status",
        "task_planning_attempts",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_task_planning_attempts_workspace_task",
        "task_planning_attempts",
        ["workspace_id", "task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_planning_attempts_workspace_task",
        table_name="task_planning_attempts",
    )
    op.drop_index(
        "ix_task_planning_attempts_workspace_status",
        table_name="task_planning_attempts",
    )
    op.drop_table("task_planning_attempts")
