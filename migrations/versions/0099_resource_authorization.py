"""Private resource ownership and workspace-member action grants."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0099_resource_authorization"
down_revision = "0098_plugin_distribution"
branch_labels = None
depends_on = None


# Immutable migration mapping. Do not import the application's evolving catalog.
_RESOURCES = {
    "team": ("agent_teams", None),
    "agent": ("agent_profiles", None),
    "project": ("workspace_projects", "created_by_user_id"),
    "domain_project": ("domain_projects", None),
    "domain_item": ("domain_items", None),
    "task": ("tasks", "created_by_user_id"),
    "capability": ("capability_resources", "created_by_user_id"),
    "mcp_server": ("mcp_servers", None),
    "mcp_tool": ("mcp_tool_allowlist", None),
    "skill": ("workspace_skill_installs", "installed_by_user_id"),
    "session": ("persistent_agent_sessions", None),
    "thread": ("agent_message_threads", None),
    "workflow": ("orchestration_definitions", "created_by_user_id"),
    "file": ("workspace_files", "uploaded_by_user_id"),
    "knowledge": ("knowledge_sources", "created_by_user_id"),
    "memory": ("workspace_memory_entries", "created_by_user_id"),
    "automation": ("automations", "created_by_user_id"),
    "runtime_space": ("runtime_spaces", "created_by_user_id"),
    "runtime": ("workspace_runtimes", None),
    "provider": ("model_provider_credentials", "created_by_user_id"),
    "webhook": ("webhook_subscriptions", "created_by_user_id"),
    "schedule": ("workspace_scheduled_jobs", "created_by_user_id"),
}


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("session_key", sa.String(240), nullable=True))
    op.create_index(
        "ix_agent_runs_workspace_session", "agent_runs", ["workspace_id", "session_key", "status"]
    )
    op.create_unique_constraint(
        "uq_automations_workspace_id", "automations", ["workspace_id", "id"]
    )
    op.add_column("automations", sa.Column("execution_identity", JSONB(), nullable=True))
    op.add_column("automation_events", sa.Column("execution_identity", JSONB(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE automations SET execution_identity = jsonb_build_object("
            "'user_id', created_by_user_id::text, 'token_id', NULL, 'token_scopes', NULL)"
        )
    )
    op.create_table(
        "external_identity_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("automation_id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.String(160), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "workspace_id", "automation_id", "sender_id", name="uq_external_identity_sender"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "automation_id"],
            ["automations.workspace_id", "automations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
        ),
    )
    op.add_column("tasks", sa.Column("execution_identity", JSONB(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE tasks SET execution_identity = jsonb_build_object("
            "'user_id', created_by_user_id::text, 'token_id', NULL, 'token_scopes', NULL) "
            "WHERE created_by_user_id IS NOT NULL"
        )
    )
    op.execute(
        sa.text("""
        WITH RECURSIVE principals AS (
            SELECT id, workspace_id, execution_identity, ARRAY[id] AS path
            FROM tasks WHERE execution_identity IS NOT NULL
            UNION ALL
            SELECT child.id, child.workspace_id, parent.execution_identity,
                   parent.path || child.id
            FROM principals parent
            JOIN agent_runs run ON run.task_id = parent.id
                AND run.workspace_id = parent.workspace_id
            JOIN tasks child ON child.created_by_agent_run_id = run.id
                AND child.workspace_id = parent.workspace_id
            WHERE child.execution_identity IS NULL AND NOT child.id = ANY(parent.path)
        )
        UPDATE tasks t SET execution_identity = p.execution_identity
        FROM principals p WHERE t.id = p.id AND t.workspace_id = p.workspace_id
            AND t.execution_identity IS NULL
    """)
    )
    op.execute(
        sa.text("""
        UPDATE automation_events e SET execution_identity = a.execution_identity
        FROM automations a WHERE a.id = e.automation_id AND a.workspace_id = e.workspace_id
            AND e.configuration->>'trigger_type' = 'schedule'
    """)
    )
    op.create_table(
        "secured_resources",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("resource_kind", sa.String(40), primary_key=True),
        sa.Column("resource_id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_secured_resources_owner", "secured_resources", ["workspace_id", "owner_user_id"]
    )
    op.create_table(
        "resource_grants",
        sa.Column("workspace_id", sa.Uuid(), primary_key=True),
        sa.Column("resource_kind", sa.String(40), primary_key=True),
        sa.Column("resource_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("action", sa.String(20), primary_key=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id", "resource_kind", "resource_id"],
            [
                "secured_resources.workspace_id",
                "secured_resources.resource_kind",
                "secured_resources.resource_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "action IN ('read','invoke','update','delete','share','control','approve')",
            name="resource_action_valid",
        ),
    )
    op.create_index(
        "ix_resource_grants_subject",
        "resource_grants",
        ["workspace_id", "user_id", "action", "resource_kind"],
    )
    for kind, (table, creator) in _RESOURCES.items():
        # Existing unattributed resources stay administrator-only. Never guess an owner.
        owner = "m.user_id" if creator else "NULL"
        membership = (
            f"LEFT JOIN workspace_members m ON m.workspace_id=r.workspace_id "
            f"AND m.user_id=r.{creator}"
            if creator
            else ""
        )
        op.execute(
            sa.text(
                "INSERT INTO secured_resources (workspace_id, resource_kind, resource_id, owner_user_id) "
                f"SELECT r.workspace_id, '{kind}', r.id, {owner} FROM {table} r {membership}"
            )
        )
    op.execute(
        sa.text("""
        UPDATE secured_resources r SET owner_user_id = m.user_id
        FROM tasks t JOIN workspace_members m ON m.workspace_id = t.workspace_id
            AND m.user_id::text = t.execution_identity->>'user_id'
        WHERE r.resource_kind = 'task' AND r.resource_id = t.id
            AND r.workspace_id = t.workspace_id AND r.owner_user_id IS NULL
    """)
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_workspace_session", table_name="agent_runs")
    op.drop_column("agent_runs", "session_key")
    op.drop_table("resource_grants")
    op.drop_table("secured_resources")
    op.drop_column("tasks", "execution_identity")
    op.drop_table("external_identity_bindings")
    op.drop_column("automation_events", "execution_identity")
    op.drop_column("automations", "execution_identity")
    op.drop_constraint("uq_automations_workspace_id", "automations", type_="unique")
