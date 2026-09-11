"""Track per-run runtime workspace cleanup."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0078_add_run_runtime_cleanup_state"
down_revision = "0077_structured_workflow_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_run_project_io_states",
        sa.Column("cleanup_status", sa.String(length=32), nullable=False, server_default="pending"),
    )
    op.add_column(
        "agent_run_project_io_states",
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_run_project_io_states",
        sa.Column("cleanup_error", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "agent_run_project_io_states",
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "cleanup_status_valid",
        "agent_run_project_io_states",
        "cleanup_status in ('pending', 'running', 'completed', 'failed', 'not_required')",
    )
    op.alter_column(
        "agent_run_project_io_states",
        "cleanup_status",
        server_default=None,
    )
    op.alter_column(
        "agent_run_project_io_states",
        "cleanup_attempts",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_constraint(
        "cleanup_status_valid",
        "agent_run_project_io_states",
        type_="check",
    )
    op.drop_column("agent_run_project_io_states", "cleaned_at")
    op.drop_column("agent_run_project_io_states", "cleanup_error")
    op.drop_column("agent_run_project_io_states", "cleanup_attempts")
    op.drop_column("agent_run_project_io_states", "cleanup_status")
