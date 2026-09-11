"""Persist ephemeral runtime containers for individual agent runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0079_per_run_runtime_execution"
down_revision = "0078_add_run_runtime_cleanup_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "execution_runtime_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_runtimes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_agent_runs_workspace_execution_runtime",
        "agent_runs",
        ["workspace_id", "execution_runtime_id"],
    )
    op.add_column(
        "workspace_runtimes",
        sa.Column(
            "parent_runtime_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_runtimes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "workspace_runtimes",
        sa.Column(
            "execution_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_workspace_runtimes_workspace_execution_run",
        "workspace_runtimes",
        ["workspace_id", "execution_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_runtimes_workspace_execution_run",
        table_name="workspace_runtimes",
    )
    op.drop_column("workspace_runtimes", "execution_run_id")
    op.drop_column("workspace_runtimes", "parent_runtime_id")
    op.drop_index("ix_agent_runs_workspace_execution_runtime", table_name="agent_runs")
    op.drop_column("agent_runs", "execution_runtime_id")
