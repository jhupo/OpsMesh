"""execution: runs, model sessions, environments and MCP connections

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18 00:00:00.000000+00:00

The execution core tables. Runs serialize per model session on their
sequence and carry encrypted frozen requests; run events are the single
progress fact with a unique event key; operation receipts make model,
tool and MCP calls idempotent per scope. Model sessions own their native
history through the official SDK session, so this migration deliberately
does not create the SDK history tables. The superseded second command
queues and the legacy sandbox reference table are not recreated: delivery
goes through the unique temporal outbox and the sandbox reference is the
environment's native session id. No historical architecture migration is
inherited.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("owner_ref", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("next_sequence", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_sessions")),
        sa.CheckConstraint(
            "status IN ('open', 'closing', 'closed', 'blocked')", name="valid_status"
        ),
        sa.CheckConstraint("next_sequence >= 1", name="positive_sequence"),
    )
    op.create_index(
        op.f("ix_execution_sessions_scope_id"), "execution_sessions", ["scope_id"], unique=False
    )
    op.create_index(
        "ix_execution_sessions_scope_owner",
        "execution_sessions",
        ["scope_id", "owner_ref"],
        unique=False,
    )
    op.create_table(
        "execution_environments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("owner_ref", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("native_session_id", sa.Uuid(), nullable=True),
        sa.Column("encrypted_state", sa.Text(), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_environments")),
        sa.CheckConstraint(
            "status IN ('pending','creating','ready','resuming',"
            "'closing','closed','needs_attention')",
            name="valid_status",
        ),
        sa.UniqueConstraint(
            "native_session_id", name=op.f("uq_execution_environments_native_session_id")
        ),
    )
    op.create_index(
        op.f("ix_execution_environments_scope_id"),
        "execution_environments",
        ["scope_id"],
        unique=False,
    )
    op.create_index(
        "ix_execution_environments_scope_owner",
        "execution_environments",
        ["scope_id", "owner_ref"],
        unique=False,
    )
    op.create_table(
        "execution_mcp_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("owner_ref", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_mcp_connections")),
        sa.CheckConstraint(
            "transport IN ('streamable_http', 'sse', 'stdio')",
            name="mcp_connection_transport",
        ),
        sa.CheckConstraint(
            "status IN ('pending','connecting','ready','failed','closed')",
            name="valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["execution_environments.id"],
            name=op.f("fk_execution_mcp_connections_environment_id_execution_environments"),
        ),
        sa.UniqueConstraint("scope_id", "name", name="uq_execution_mcp_connections_scope_name"),
    )
    op.create_index(
        op.f("ix_execution_mcp_connections_scope_id"),
        "execution_mcp_connections",
        ["scope_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_mcp_connections_environment_id"),
        "execution_mcp_connections",
        ["environment_id"],
        unique=False,
    )
    op.create_table(
        "execution_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("owner_ref", sa.String(length=200), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_ciphertext", sa.Text(), nullable=False),
        sa.Column("result_ciphertext", sa.Text(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_runs")),
        sa.CheckConstraint(
            "status IN ('queued','preparing','running','waiting_approval','cancelling',"
            "'finalizing','succeeded','failed','cancelled','timed_out','needs_attention')",
            name="run_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["execution_sessions.id"],
            name=op.f("fk_execution_runs_session_id_execution_sessions"),
        ),
        sa.UniqueConstraint("session_id", "sequence", name="uq_execution_runs_session_sequence"),
    )
    op.create_index(
        op.f("ix_execution_runs_scope_id"), "execution_runs", ["scope_id"], unique=False
    )
    op.create_index(
        "ix_execution_runs_scope_owner",
        "execution_runs",
        ["scope_id", "owner_ref", "created_at"],
        unique=False,
    )
    op.create_table(
        "execution_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(length=240), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_run_events")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["execution_runs.id"],
            name=op.f("fk_execution_run_events_run_id_execution_runs"),
        ),
        sa.UniqueConstraint("event_key", name=op.f("uq_execution_run_events_event_key")),
        sa.UniqueConstraint("run_id", "sequence", name="uq_execution_run_events_run_sequence"),
    )
    op.create_index(
        op.f("ix_execution_run_events_scope_id"),
        "execution_run_events",
        ["scope_id"],
        unique=False,
    )
    op.create_table(
        "execution_run_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_key", sa.String(length=240), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("state_ciphertext", sa.Text(), nullable=False),
        sa.Column("interruptions_ciphertext", sa.Text(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_run_approvals")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["execution_runs.id"],
            name=op.f("fk_execution_run_approvals_run_id_execution_runs"),
        ),
        sa.UniqueConstraint(
            "run_id", "checkpoint_key", name="uq_execution_run_approvals_run_checkpoint"
        ),
    )
    op.create_index(
        op.f("ix_execution_run_approvals_scope_id"),
        "execution_run_approvals",
        ["scope_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_run_approvals_approval_id"),
        "execution_run_approvals",
        ["approval_id"],
        unique=False,
    )
    op.create_table(
        "execution_operation_receipts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.String(length=240), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("accounted", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_ciphertext", sa.Text(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint(
            "scope_id", "operation_id", name=op.f("pk_execution_operation_receipts")
        ),
        sa.CheckConstraint("kind IN ('model', 'tool', 'mcp')", name="run_receipt_kind"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["execution_runs.id"],
            name=op.f("fk_execution_operation_receipts_run_id_execution_runs"),
        ),
    )
    op.create_index(
        op.f("ix_execution_operation_receipts_run_id"),
        "execution_operation_receipts",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "execution_session_operation_receipts",
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.String(length=512), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "scope_id", "operation_id", name=op.f("pk_execution_session_operation_receipts")
        ),
        sa.CheckConstraint(
            "method IN ('get_items', 'add_items', 'pop_item', 'clear_session')",
            name="session_receipt_method",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["execution_sessions.id"],
            ondelete="CASCADE",
            name=op.f("fk_execution_session_operation_receipts_session_id_execution_sessions"),
        ),
    )
    op.create_index(
        op.f("ix_execution_session_operation_receipts_session_id"),
        "execution_session_operation_receipts",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "execution_environment_operation_receipts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.String(length=240), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("encrypted_result", sa.Text(), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint(
            "scope_id", "operation_id", name=op.f("pk_execution_environment_operation_receipts")
        ),
        sa.CheckConstraint(
            "action IN ('create', 'resume', 'close')",
            name="environment_receipt_action",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'uncertain')",
            name="valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["execution_environments.id"],
            ondelete="CASCADE",
            name=op.f(
                "fk_execution_environment_operation_receipts_environment_id_execution_environments"
            ),
        ),
    )
    op.create_index(
        op.f("ix_execution_environment_operation_receipts_environment_id"),
        "execution_environment_operation_receipts",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_environment_operation_receipts_run_id"),
        "execution_environment_operation_receipts",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "execution_mcp_operation_receipts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.String(length=240), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "scope_id", "operation_id", name=op.f("pk_execution_mcp_operation_receipts")
        ),
        sa.CheckConstraint(
            "action IN ('connect', 'discover', 'close')",
            name="mcp_receipt_action",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'uncertain')",
            name="valid_status",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["execution_mcp_connections.id"],
            ondelete="CASCADE",
            name=op.f("fk_execution_mcp_operation_receipts_connection_id_execution_mcp_connections"),
        ),
    )
    op.create_index(
        op.f("ix_execution_mcp_operation_receipts_connection_id"),
        "execution_mcp_operation_receipts",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_mcp_operation_receipts_run_id"),
        "execution_mcp_operation_receipts",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_execution_mcp_operation_receipts_run_id"),
        table_name="execution_mcp_operation_receipts",
    )
    op.drop_index(
        op.f("ix_execution_mcp_operation_receipts_connection_id"),
        table_name="execution_mcp_operation_receipts",
    )
    op.drop_table("execution_mcp_operation_receipts")
    op.drop_index(
        op.f("ix_execution_environment_operation_receipts_run_id"),
        table_name="execution_environment_operation_receipts",
    )
    op.drop_index(
        op.f("ix_execution_environment_operation_receipts_environment_id"),
        table_name="execution_environment_operation_receipts",
    )
    op.drop_table("execution_environment_operation_receipts")
    op.drop_index(
        op.f("ix_execution_session_operation_receipts_session_id"),
        table_name="execution_session_operation_receipts",
    )
    op.drop_table("execution_session_operation_receipts")
    op.drop_index(
        op.f("ix_execution_operation_receipts_run_id"),
        table_name="execution_operation_receipts",
    )
    op.drop_table("execution_operation_receipts")
    op.drop_index(
        op.f("ix_execution_run_approvals_approval_id"),
        table_name="execution_run_approvals",
    )
    op.drop_index(
        op.f("ix_execution_run_approvals_scope_id"), table_name="execution_run_approvals"
    )
    op.drop_table("execution_run_approvals")
    op.drop_index(op.f("ix_execution_run_events_scope_id"), table_name="execution_run_events")
    op.drop_table("execution_run_events")
    op.drop_index("ix_execution_runs_scope_owner", table_name="execution_runs")
    op.drop_index(op.f("ix_execution_runs_scope_id"), table_name="execution_runs")
    op.drop_table("execution_runs")
    op.drop_index(
        op.f("ix_execution_mcp_connections_environment_id"),
        table_name="execution_mcp_connections",
    )
    op.drop_index(
        op.f("ix_execution_mcp_connections_scope_id"), table_name="execution_mcp_connections"
    )
    op.drop_table("execution_mcp_connections")
    op.drop_index("ix_execution_environments_scope_owner", table_name="execution_environments")
    op.drop_index(
        op.f("ix_execution_environments_scope_id"), table_name="execution_environments"
    )
    op.drop_table("execution_environments")
    op.drop_index("ix_execution_sessions_scope_owner", table_name="execution_sessions")
    op.drop_index(op.f("ix_execution_sessions_scope_id"), table_name="execution_sessions")
    op.drop_table("execution_sessions")
