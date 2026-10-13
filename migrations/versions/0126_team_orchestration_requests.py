"""Persist explicit and event-triggered Team orchestration requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0126_team_orchestration_requests"
down_revision = "0125_temporal_dispatch_session_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE agent_teams SET default_task_policy = jsonb_set(
            default_task_policy, '{team_runtime}',
            (default_task_policy->'team_runtime') - ARRAY[
                'last_iteration', 'iteration_count', 'continued_from_iteration',
                'last_heartbeat_at', 'heartbeat_status', 'last_worker_failure',
                'stall_count', 'stall_reason', 'stalled_at', 'stall_threshold'
            ] || jsonb_strip_nulls(jsonb_build_object(
                'last_advancement',
                    ((default_task_policy#>'{team_runtime,last_iteration}') - 'iteration')
                    || jsonb_build_object('advancement',
                        default_task_policy#>'{team_runtime,last_iteration,iteration}'),
                'advancement_count', default_task_policy#>'{team_runtime,iteration_count}',
                'continued_from_advancement',
                    ((default_task_policy#>'{team_runtime,continued_from_iteration}') - 'iteration')
                    || jsonb_build_object('advancement',
                        default_task_policy#>'{team_runtime,continued_from_iteration,iteration}')
            ))
        ) WHERE jsonb_typeof(default_task_policy->'team_runtime') = 'object'
    """)
    op.create_table(
        "team_orchestration_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("execution_identity", postgresql.JSONB(), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'completed', 'failed', 'unknown')",
            name="team_orchestration_request_status",
        ),
    )
    op.create_index(
        "ix_team_orchestration_requests_team",
        "team_orchestration_requests",
        ["workspace_id", "team_id", "created_at"],
    )


def downgrade() -> None:
    op.execute("""
        UPDATE agent_teams SET default_task_policy = jsonb_set(
            default_task_policy, '{team_runtime}',
            (default_task_policy->'team_runtime') - ARRAY[
                'last_advancement', 'advancement_count', 'continued_from_advancement'
            ] || jsonb_strip_nulls(jsonb_build_object(
                'last_iteration',
                    ((default_task_policy#>'{team_runtime,last_advancement}') - 'advancement')
                    || jsonb_build_object('iteration',
                        default_task_policy#>'{team_runtime,last_advancement,advancement}'),
                'iteration_count', default_task_policy#>'{team_runtime,advancement_count}',
                'continued_from_iteration',
                    ((default_task_policy#>'{team_runtime,continued_from_advancement}') - 'advancement')
                    || jsonb_build_object('iteration',
                        default_task_policy#>'{team_runtime,continued_from_advancement,advancement}')
            ))
        ) WHERE jsonb_typeof(default_task_policy->'team_runtime') = 'object'
    """)
    op.drop_index("ix_team_orchestration_requests_team", table_name="team_orchestration_requests")
    op.drop_table("team_orchestration_requests")
