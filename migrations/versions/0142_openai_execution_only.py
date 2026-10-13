"""Retire non-OpenAI execution settings without rewriting historical Run data."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0142_openai_execution_only"
down_revision = "0141_archive_export_uncertainty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Refuse to strand an admitted execution that still needs the removed SDK.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_runs WHERE status IN "
        "('queued','running','waiting_approval') AND "
        "input #>> '{authorization_snapshot,model_provider,provider}' NOT IN "
        "('openai','openai-compatible')) THEN RAISE EXCEPTION "
        "'Drain non-OpenAI Agent runs before retiring their SDK'; END IF; END $$"
    )
    op.create_table(
        "retired_agent_provider_configuration",
        sa.Column("entity_kind", sa.String(32), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", postgresql.JSONB(), nullable=False),
    )
    op.execute(
        "INSERT INTO retired_agent_provider_configuration "
        "SELECT 'workspace', id, id, jsonb_build_object('approvals', settings->'approvals') "
        "FROM workspaces WHERE settings #> '{approvals,claude_permission_mode}' IS NOT NULL "
        "OR jsonb_path_exists(settings, '$.approvals.rules[*] ? (@.action == \"model_request\")')"
    )
    op.execute(
        "UPDATE workspaces SET settings = settings #- '{approvals,claude_permission_mode}' "
        "WHERE settings #> '{approvals,claude_permission_mode}' IS NOT NULL"
    )
    op.execute(
        "UPDATE workspaces SET settings=jsonb_set(settings,'{approvals,rules}', "
        "(SELECT COALESCE(jsonb_agg(rule),'[]'::jsonb) FROM "
        "jsonb_array_elements(settings #> '{approvals,rules}') AS rule "
        "WHERE rule->>'action' IS DISTINCT FROM 'model_request')) "
        "WHERE jsonb_typeof(settings #> '{approvals,rules}')='array' AND "
        "jsonb_path_exists(settings, '$.approvals.rules[*] ? (@.action == \"model_request\")')"
    )
    op.execute(
        "INSERT INTO retired_agent_provider_configuration SELECT 'credential', id, workspace_id, "
        "jsonb_build_object('status',status,'is_default',is_default) "
        "FROM model_provider_credentials WHERE provider NOT IN ('openai','openai-compatible')"
    )
    op.execute(
        "UPDATE model_provider_credentials SET status='disabled', is_default=false "
        "WHERE provider NOT IN ('openai','openai-compatible')"
    )
    op.execute(
        "INSERT INTO retired_agent_provider_configuration SELECT 'session', s.id, s.workspace_id, "
        "jsonb_build_object('status',s.status,'sdk_provider',s.metadata->'sdk_provider', "
        "'sdk_history_watermark',COALESCE((SELECT max(m.id) FROM sdk_agent_messages AS m "
        "WHERE m.session_id=s.session_key),0)) "
        "FROM persistent_agent_sessions AS s WHERE s.metadata ? 'sdk_provider'"
    )
    op.execute(
        "UPDATE persistent_agent_sessions SET status = CASE WHEN "
        "metadata->>'sdk_provider' <> 'openai_agents' THEN 'retired' ELSE status END, "
        "metadata = metadata - 'sdk_provider' WHERE metadata ? 'sdk_provider'"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM retired_agent_provider_configuration AS r "
        "JOIN persistent_agent_sessions AS s ON s.id=r.entity_id "
        "WHERE r.entity_kind='session' AND r.value->>'sdk_provider'<>'openai_agents' "
        "AND EXISTS (SELECT 1 FROM sdk_agent_messages AS m WHERE m.session_id=s.session_key "
        "AND m.id>(r.value->>'sdk_history_watermark')::bigint)) THEN RAISE EXCEPTION "
        "'Remove reinitialized retired sessions before downgrading providers'; END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM retired_agent_provider_configuration AS r "
        "JOIN model_provider_credentials AS c ON c.id=r.entity_id "
        "WHERE r.entity_kind='credential' AND r.value->>'status'='active' AND "
        "(r.value->>'is_default')::boolean AND EXISTS (SELECT 1 FROM "
        "model_provider_credentials AS current WHERE current.workspace_id=c.workspace_id "
        "AND current.id<>c.id AND current.status='active' AND current.is_default)) "
        "THEN RAISE EXCEPTION 'Unset the new active default credential before downgrading "
        "retired providers'; END IF; END $$"
    )
    op.execute(
        "UPDATE workspaces AS w SET settings=jsonb_set(w.settings,'{approvals}', "
        "r.value->'approvals') "
        "FROM retired_agent_provider_configuration AS r "
        "WHERE r.entity_kind='workspace' AND r.entity_id=w.id"
    )
    op.execute(
        "UPDATE model_provider_credentials AS c SET status=r.value->>'status', "
        "is_default=(r.value->>'is_default')::boolean "
        "FROM retired_agent_provider_configuration AS r "
        "WHERE r.entity_kind='credential' AND r.entity_id=c.id"
    )
    op.execute(
        "UPDATE persistent_agent_sessions AS s SET status=r.value->>'status', "
        "metadata=s.metadata || jsonb_build_object('sdk_provider',r.value->'sdk_provider') "
        "FROM retired_agent_provider_configuration AS r "
        "WHERE r.entity_kind='session' AND r.entity_id=s.id"
    )
    op.drop_table("retired_agent_provider_configuration")
