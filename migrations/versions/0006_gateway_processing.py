"""Gateway policy context, event cursors and isolated workflow conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0006_gateway_processing"
down_revision = "0005_platform_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gateway_channels", sa.Column("send_window_started", sa.DateTime(timezone=True)))
    op.add_column(
        "gateway_channels",
        sa.Column("send_window_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("gateway_channels", "send_window_count", server_default=None)
    op.add_column(
        "gateway_routes",
        sa.Column("output_mode", sa.String(16), nullable=False, server_default="text"),
    )
    op.alter_column("gateway_routes", "output_mode", server_default=None)
    op.add_column(
        "gateway_inbox",
        sa.Column("conversation_key", sa.String(64), nullable=False, server_default=""),
    )
    op.alter_column("gateway_inbox", "conversation_key", server_default=None)
    op.create_index("ix_gateway_inbox_conversation_key", "gateway_inbox", ["conversation_key"])
    op.create_index(
        "ix_gateway_inbox_session_queue",
        "gateway_inbox",
        ["channel_id", "identity_id", "conversation_key", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_gateway_inbox_group_history",
        "gateway_inbox",
        ["channel_id", "conversation_key", "status", "created_at", "id"],
    )
    op.add_column("gateway_inbox", sa.Column("encrypted_context", sa.Text()))
    op.add_column("gateway_inbox", sa.Column("control", sa.String(16)))
    op.add_column(
        "gateway_inbox", sa.Column("event_cursor", sa.Integer(), nullable=False, server_default="0")
    )
    op.alter_column("gateway_inbox", "event_cursor", server_default=None)
    op.add_column(
        "gateway_deliveries",
        sa.Column("kind", sa.String(16), nullable=False, server_default="message"),
    )
    op.alter_column("gateway_deliveries", "kind", server_default=None)
    op.create_table(
        "gateway_pushes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("gateway_channels.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("encrypted_result", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_gateway_pushes_channel_id", "gateway_pushes", ["channel_id"])
    op.create_table(
        "gateway_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encrypted_state", sa.Text()),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("gateway_channels.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_gateway_bindings_owner_id", "gateway_bindings", ["owner_id"])
    op.create_table(
        "workflow_agent_sessions",
        sa.Column("scope_key", sa.String(64), primary_key=True),
        sa.Column("binding", sa.String(100), primary_key=True),
        sa.Column(
            "instance_id", sa.Uuid(), sa.ForeignKey("execution_instances.id"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("execution_sessions.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("workflow_agent_sessions")
    op.drop_table("gateway_bindings")
    op.drop_table("gateway_pushes")
    op.drop_column("gateway_deliveries", "kind")
    op.drop_column("gateway_inbox", "event_cursor")
    op.drop_column("gateway_inbox", "encrypted_context")
    op.drop_column("gateway_inbox", "control")
    op.drop_index("ix_gateway_inbox_group_history", table_name="gateway_inbox")
    op.drop_index("ix_gateway_inbox_session_queue", table_name="gateway_inbox")
    op.drop_index("ix_gateway_inbox_conversation_key", table_name="gateway_inbox")
    op.drop_column("gateway_inbox", "conversation_key")
    op.drop_column("gateway_routes", "output_mode")
    op.drop_column("gateway_channels", "send_window_count")
    op.drop_column("gateway_channels", "send_window_started")
