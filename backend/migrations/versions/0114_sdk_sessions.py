"""Use native SDK session tables and remove duplicated provider history state.

Revision ID: 0114_sdk_sessions
Revises: 0113_database_configuration
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0114_sdk_sessions"
down_revision = "0113_database_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE persistent_agent_sessions s SET metadata = metadata ||
            jsonb_build_object('sdk_provider', CASE WHEN EXISTS (
                SELECT 1 FROM persistent_agent_session_items i
                WHERE i.persistent_session_id = s.id
                    AND i.item->>'_opsmesh_runtime' = 'claude_agent_sdk'
            ) THEN 'claude_agent_sdk' ELSE 'openai_agents' END)
    """)
    op.add_column("self_hosted_mcp_jobs", sa.Column("tool_call_id", sa.String(240)))
    op.create_unique_constraint(
        "uq_self_hosted_mcp_sdk_call", "self_hosted_mcp_jobs", ["agent_run_id", "tool_call_id"]
    )
    op.drop_constraint(
        "uq_agent_sessions_workspace_key", "persistent_agent_sessions", type_="unique"
    )
    op.create_unique_constraint(
        "uq_agent_sessions_key", "persistent_agent_sessions", ["session_key"]
    )
    op.create_table(
        "sdk_agent_sessions",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["persistent_agent_sessions.session_key"], ondelete="CASCADE"
        ),
    )
    op.create_table(
        "sdk_agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("message_data", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sdk_agent_sessions.session_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "idx_sdk_agent_messages_session_time", "sdk_agent_messages", ["session_id", "created_at"]
    )
    op.execute("""
        INSERT INTO sdk_agent_sessions (session_id, created_at, updated_at)
        SELECT session_key, created_at AT TIME ZONE 'UTC', updated_at AT TIME ZONE 'UTC'
        FROM persistent_agent_sessions
    """)
    op.execute("""
        INSERT INTO sdk_agent_messages (session_id, message_data, created_at)
        SELECT s.session_key, i.item::text, i.created_at AT TIME ZONE 'UTC'
        FROM persistent_agent_session_items i
        JOIN persistent_agent_sessions s ON s.id = i.persistent_session_id
        ORDER BY s.session_key, i.sequence
    """)
    op.drop_table("persistent_agent_session_items")
    op.drop_column("persistent_agent_sessions", "openai_conversation_id")
    op.execute("""
        UPDATE agent_runs SET output = jsonb_set(
            output, '{raw_output}',
            (output->'raw_output') - 'resume_input' - 'resume_input_error'
                - 'sdk_continuation' - 'conversation_id' - 'last_response_id'
        ) WHERE jsonb_typeof(output->'raw_output') = 'object'
    """)
    op.execute(
        "UPDATE agent_runs SET input = input - 'pending_tool_results' WHERE input ? 'pending_tool_results'"
    )
    op.execute(
        "UPDATE agent_runs SET output = output - 'sdk_continuation' WHERE output ? 'sdk_continuation'"
    )
    op.execute("""
        DELETE FROM workspace_memory_entries
        WHERE memory_layer = 'working' AND source_type = 'run_working_memory'
            AND (memory_key IN ('objective', 'plan') OR entry_type = 'tool_result')
    """)


def downgrade() -> None:
    op.drop_constraint("uq_self_hosted_mcp_sdk_call", "self_hosted_mcp_jobs", type_="unique")
    op.drop_column("self_hosted_mcp_jobs", "tool_call_id")
    # Restore the old storage schema and history. Removed redundant snapshots are not regenerated.
    op.add_column("persistent_agent_sessions", sa.Column("openai_conversation_id", sa.String(240)))
    op.create_table(
        "persistent_agent_session_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("persistent_session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("item", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["persistent_session_id"], ["persistent_agent_sessions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "persistent_session_id", "sequence", name="uq_agent_session_items_session_sequence"
        ),
    )
    op.create_index(
        "ix_agent_session_items_workspace_session",
        "persistent_agent_session_items",
        ["workspace_id", "persistent_session_id"],
    )
    op.execute("""
        INSERT INTO persistent_agent_session_items
            (id, workspace_id, persistent_session_id, sequence, item, created_at)
        SELECT gen_random_uuid(), s.workspace_id, s.id,
            row_number() OVER (PARTITION BY s.id ORDER BY m.id),
            m.message_data::jsonb, m.created_at AT TIME ZONE 'UTC'
        FROM sdk_agent_messages m
        JOIN persistent_agent_sessions s ON s.session_key = m.session_id
    """)
    op.drop_table("sdk_agent_messages")
    op.drop_table("sdk_agent_sessions")
    op.drop_constraint("uq_agent_sessions_key", "persistent_agent_sessions", type_="unique")
    op.create_unique_constraint(
        "uq_agent_sessions_workspace_key",
        "persistent_agent_sessions",
        ["workspace_id", "session_key"],
    )
