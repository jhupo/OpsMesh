"""Preserve product execution instants independently of the database timezone."""

import sqlalchemy as sa
from alembic import op

revision = "0138_execution_timestamps"
down_revision = "0137_retired_model_fallback"
branch_labels = None
depends_on = None

# SDK-owned Session tables retain their native schema. These are product facts.
TIMESTAMPS = (
    ("platform_policy_events", "created_at", False),
    ("security_events", "created_at", False),
    ("model_provider_credentials", "last_success_at", True),
    ("model_provider_credentials", "last_failure_at", True),
    ("mcp_servers", "last_health_check_at", True),
    ("mcp_tool_call_logs", "created_at", False),
    ("agent_runs", "started_at", True),
    ("agent_runs", "completed_at", True),
    ("run_events", "created_at", False),
    ("tasks", "completed_at", True),
    ("task_planning_attempts", "created_at", False),
    ("task_planning_attempts", "completed_at", True),
    ("revision_requests", "resolved_at", True),
    ("artifacts", "created_at", False),
    ("file_access_events", "created_at", False),
    ("workspace_reservations", "expires_at", True),
    ("workspace_reservations", "released_at", True),
    ("runtime_templates", "created_at", False),
    ("runtime_events", "created_at", False),
    ("runtime_commands", "started_at", True),
    ("runtime_commands", "completed_at", True),
    ("sandbox_references", "completed_at", True),
    ("runtime_space_reservations", "expires_at", True),
    ("runtime_space_reservations", "released_at", True),
    ("runtime_space_events", "created_at", False),
)


def upgrade() -> None:
    # Previous writes cast aware timestamps to the database's local wall clock.
    for table, column, nullable in TIMESTAMPS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=False),
            type_=sa.DateTime(timezone=True),
            existing_nullable=nullable,
            postgresql_using=f"{column} AT TIME ZONE current_setting('TimeZone')",
        )


def downgrade() -> None:
    for table, column, nullable in reversed(TIMESTAMPS):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(timezone=False),
            existing_nullable=nullable,
            postgresql_using=f"{column} AT TIME ZONE current_setting('TimeZone')",
        )
