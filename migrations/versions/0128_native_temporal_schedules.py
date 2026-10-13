"""Replace queue schedule fields and persist reply delivery lifecycle."""

import sqlalchemy as sa
from alembic import op

revision = "0128_native_temporal_schedules"
down_revision = "0127_task_event_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_automations_due", table_name="automations")
    op.drop_index("ix_automation_events_pending", table_name="automation_events")
    op.drop_index("ix_automation_events_scan", table_name="automation_events")
    op.drop_column("automation_events", "checked_at")
    op.drop_index("ix_workspace_scheduled_jobs_due", table_name="workspace_scheduled_jobs")
    op.alter_column("workspace_scheduled_jobs", "job_type", new_column_name="execution_kind")
    op.alter_column("workspace_scheduled_jobs", "routing", new_column_name="parameters")
    op.drop_column("workspace_scheduled_jobs", "priority")
    op.drop_column("workspace_scheduled_jobs", "max_attempts")
    op.add_column(
        "workspace_scheduled_jobs",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("""UPDATE workspace_scheduled_jobs SET execution_kind = CASE execution_kind
        WHEN 'agent.run' THEN 'agent' WHEN 'task.plan' THEN 'task_plan'
        WHEN 'workspace.archive_export' THEN 'archive_export' WHEN 'memory.index' THEN 'memory_index'
        WHEN 'webhook.delivery' THEN 'webhook_delivery' WHEN 'secret.reencrypt' THEN 'secret_reencrypt'
        WHEN 'model_provider.health_check' THEN 'model_provider_health' ELSE execution_kind END,
        action_type = CASE WHEN action_type = 'queue_job' THEN 'execute' ELSE action_type END""")
    # Retired queue-only operations cannot be silently reinterpreted as native SDK calls.
    op.execute("""UPDATE workspace_scheduled_jobs SET status = 'paused', next_run_at = NULL
        WHERE execution_kind IN ('mcp.tool_execution', 'team.execution_loop', 'runtime.cleanup')""")
    op.alter_column(
        "workspace_scheduled_job_events", "queued_job_id", new_column_name="execution_id"
    )
    op.add_column("workspace_scheduled_job_events", sa.Column("occurrence_id", sa.String(255)))
    op.execute("UPDATE workspace_scheduled_job_events SET occurrence_id = 'retained:' || id::text")
    op.alter_column("workspace_scheduled_job_events", "occurrence_id", nullable=False)
    op.create_unique_constraint(
        "uq_schedule_occurrence",
        "workspace_scheduled_job_events",
        ["scheduled_job_id", "occurrence_id"],
    )
    op.add_column(
        "automation_events",
        sa.Column("reply_final", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE automation_events SET reply_final = true WHERE status IN ('reply_pending', 'completed', 'reply_failed')"
    )
    # Retained active definitions become native schedule commands, rather than
    # relying on a removed maintenance scanner after the upgrade.
    op.execute("""WITH retained AS (
        SELECT gen_random_uuid() AS dispatch_id, j.workspace_id, j.id AS resource_id,
            COALESCE(j.created_by_user_id, w.owner_user_id) AS actor_id,
            'scheduled_job' AS owner_kind
        FROM workspace_scheduled_jobs j JOIN workspaces w ON w.id = j.workspace_id
        WHERE j.status IN ('active', 'paused')
        UNION ALL
        SELECT gen_random_uuid(), a.workspace_id, a.id, a.created_by_user_id, 'automation'
        FROM automations a WHERE a.configuration->>'trigger_type' = 'schedule'
    ) INSERT INTO temporal_dispatches
        (id, workspace_id, workflow_id, operation, payload, available_at, attempts)
    SELECT dispatch_id, workspace_id,
        'opsmesh:' || workspace_id || ':schedule_control:' || dispatch_id, 'start',
        jsonb_build_object('dispatch_id', dispatch_id, 'workspace_id', workspace_id,
            'workflow_id', 'opsmesh:' || workspace_id || ':schedule_control:' || dispatch_id,
            'operation', 'start', 'execution', jsonb_build_object(
                'execution_id', dispatch_id, 'workspace_id', workspace_id,
                'resource_id', resource_id, 'kind', 'schedule_control',
                'requested_by_user_id', actor_id,
                'parameters', jsonb_build_object('owner_kind', owner_kind))), now(), 0
    FROM retained""")
    op.execute("""WITH retained AS (
        SELECT gen_random_uuid() AS dispatch_id, workspace_id, id AS resource_id,
            CASE WHEN status = 'pending' THEN 'entry_event' ELSE 'reply_delivery' END AS kind
        FROM automation_events WHERE status IN
            ('pending', 'dispatched', 'control_applied', 'reply_pending')
    ) INSERT INTO temporal_dispatches
        (id, workspace_id, workflow_id, operation, payload, available_at, attempts)
    SELECT dispatch_id, workspace_id,
        'opsmesh:' || workspace_id || ':' || kind || ':' || resource_id, 'start',
        jsonb_build_object('dispatch_id', dispatch_id, 'workspace_id', workspace_id,
            'workflow_id', 'opsmesh:' || workspace_id || ':' || kind || ':' || resource_id,
            'operation', 'start', 'execution', jsonb_build_object(
                'execution_id', resource_id, 'workspace_id', workspace_id,
                'resource_id', resource_id, 'kind', kind, 'parameters', '{}'::jsonb)), now(), 0
    FROM retained""")


def downgrade() -> None:
    op.add_column("automation_events", sa.Column("checked_at", sa.DateTime(timezone=True)))
    op.create_index("ix_automations_due", "automations", ["status", "next_due_at"])
    op.create_index("ix_automation_events_pending", "automation_events", ["status", "created_at"])
    op.create_index(
        "ix_automation_events_scan", "automation_events", ["status", "checked_at", "created_at"]
    )
    op.create_index(
        "ix_workspace_scheduled_jobs_due", "workspace_scheduled_jobs", ["status", "next_run_at"]
    )
    op.execute("""DELETE FROM temporal_dispatches WHERE payload->'execution'->>'kind'
        IN ('schedule_control', 'schedule_tick', 'entry_event', 'reply_delivery')""")
    op.drop_column("automation_events", "reply_final")
    op.drop_constraint("uq_schedule_occurrence", "workspace_scheduled_job_events", type_="unique")
    op.drop_column("workspace_scheduled_job_events", "occurrence_id")
    op.alter_column(
        "workspace_scheduled_job_events", "execution_id", new_column_name="queued_job_id"
    )
    op.execute("""UPDATE workspace_scheduled_jobs SET execution_kind = CASE execution_kind
        WHEN 'agent' THEN 'agent.run' WHEN 'task_plan' THEN 'task.plan'
        WHEN 'archive_export' THEN 'workspace.archive_export' WHEN 'memory_index' THEN 'memory.index'
        WHEN 'webhook_delivery' THEN 'webhook.delivery' WHEN 'secret_reencrypt' THEN 'secret.reencrypt'
        WHEN 'model_provider_health' THEN 'model_provider.health_check' ELSE execution_kind END,
        action_type = CASE WHEN action_type = 'execute' THEN 'queue_job' ELSE action_type END""")
    op.drop_column("workspace_scheduled_jobs", "revision")
    op.add_column(
        "workspace_scheduled_jobs",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workspace_scheduled_jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.alter_column("workspace_scheduled_jobs", "parameters", new_column_name="routing")
    op.alter_column("workspace_scheduled_jobs", "execution_kind", new_column_name="job_type")
