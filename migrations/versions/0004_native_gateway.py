"""Native gateway identity and durable delivery; preserve legacy routes for review.

Legacy sender IDs do not carry enough scope to infer safe account bindings.
Existing channels are disabled; their encrypted settings and old tables are retained.
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0004_native_gateway"
down_revision = "0003_workflow_instances"
branch_labels = None
depends_on = None


def col(name: str, type_: Any, reference: str | None = None, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(
        name,
        type_,
        *([sa.ForeignKey(reference)] if reference else []),
        nullable=kwargs.pop("nullable", False),
        **kwargs,
    )


def timestamps() -> list[sa.Column[Any]]:
    return [
        col(name, sa.DateTime(timezone=True), server_default=sa.func.now())
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.add_column("gateway_channels", col("revision", sa.Integer(), server_default="1"))
    op.add_column("gateway_channels", col("deleted", sa.Boolean(), server_default=sa.false()))
    op.alter_column("gateway_channels", "revision", server_default=None)
    op.alter_column("gateway_channels", "deleted", server_default=None)
    op.execute("UPDATE gateway_channels SET enabled = false")
    op.add_column("workflow_runs", col("encrypted_context", sa.Text(), nullable=True))
    op.create_table(
        "gateway_identities",
        col("id", sa.UUID(), primary_key=True),
        col("channel_id", sa.UUID(), "gateway_channels.id"),
        col("identity_key", sa.String(64)),
        col("scope", sa.String(300)),
        col("id_kind", sa.String(64)),
        col("external_id", sa.String(500)),
        col("encrypted_profile", sa.Text()),
        col("key_id", sa.String(100)),
        *timestamps(),
        sa.UniqueConstraint("channel_id", "identity_key"),
    )
    op.create_index("ix_gateway_identities_channel_id", "gateway_identities", ["channel_id"])
    op.create_table(
        "gateway_account_bindings",
        col("identity_id", sa.UUID(), "gateway_identities.id", primary_key=True),
        col("user_id", sa.UUID(), "users.id"),
        col("verified_by", sa.UUID(), "users.id"),
        *timestamps(),
    )
    op.create_index("ix_gateway_account_bindings_user_id", "gateway_account_bindings", ["user_id"])
    op.create_table(
        "gateway_conversations",
        col("channel_id", sa.UUID(), "gateway_channels.id", primary_key=True),
        col("conversation_key", sa.String(64), primary_key=True),
        col("external_id", sa.String(500)),
        col("kind", sa.String(16)),
        col("encrypted_profile", sa.Text()),
        col("key_id", sa.String(100)),
        *timestamps(),
    )
    op.create_table(
        "gateway_members",
        col("identity_id", sa.UUID(), "gateway_identities.id", primary_key=True),
        col("conversation_key", sa.String(64), primary_key=True),
        col("external_role", sa.String(64), nullable=True),
        col("source", sa.String(32)),
        *timestamps(),
    )
    op.create_table(
        "gateway_routes",
        col("id", sa.UUID(), primary_key=True),
        col("channel_id", sa.UUID(), "gateway_channels.id"),
        col("identity_id", sa.UUID(), "gateway_identities.id"),
        col("conversation_id", sa.String(500)),
        col("target_kind", sa.String(16)),
        col("target_id", sa.UUID()),
        col("allow_group", sa.Boolean()),
        col("enabled", sa.Boolean()),
        col("revision", sa.Integer()),
        *timestamps(),
        sa.UniqueConstraint("identity_id", "conversation_id"),
    )
    op.create_index("ix_gateway_routes_channel_id", "gateway_routes", ["channel_id"])
    op.create_table(
        "gateway_sessions",
        col("route_id", sa.UUID(), "gateway_routes.id", primary_key=True),
        col("conversation_key", sa.String(64), primary_key=True),
        col("user_id", sa.UUID(), "users.id"),
        col("session_id", sa.UUID(), "execution_sessions.id"),
        *timestamps(),
    )
    op.create_table(
        "gateway_inbox",
        col("id", sa.UUID(), primary_key=True),
        col("channel_id", sa.UUID(), "gateway_channels.id"),
        col("identity_id", sa.UUID(), "gateway_identities.id"),
        col("event_key", sa.String(64)),
        col("encrypted_payload", sa.Text()),
        col("key_id", sa.String(100)),
        col("status", sa.String(24)),
        col("run_id", sa.UUID(), nullable=True),
        col("route_id", sa.UUID(), "gateway_routes.id", nullable=True),
        col("route_revision", sa.Integer(), nullable=True),
        col("user_id", sa.UUID(), "users.id", nullable=True),
        col("error_code", sa.String(100), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("channel_id", "event_key"),
    )
    op.create_index("ix_gateway_inbox_channel_id", "gateway_inbox", ["channel_id"])
    op.create_index("ix_gateway_inbox_status", "gateway_inbox", ["status"])
    op.create_table(
        "gateway_deliveries",
        col("id", sa.UUID(), primary_key=True),
        col("channel_id", sa.UUID(), "gateway_channels.id"),
        col("inbox_id", sa.UUID(), "gateway_inbox.id", nullable=True),
        col("ordinal", sa.Integer()),
        col("encrypted_payload", sa.Text()),
        col("key_id", sa.String(100)),
        col("status", sa.String(24)),
        col("attempts", sa.Integer()),
        col("remote_id", sa.String(500), nullable=True),
        col("error_code", sa.String(100), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("inbox_id", "ordinal"),
    )
    op.create_index("ix_gateway_deliveries_channel_id", "gateway_deliveries", ["channel_id"])
    op.create_index("ix_gateway_deliveries_status", "gateway_deliveries", ["status"])
    op.create_table(
        "gateway_leases",
        col("channel_id", sa.UUID(), "gateway_channels.id", primary_key=True),
        col("owner", sa.String(64)),
        col("expires_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Native gateway downgrade requires an explicit data-preserving recovery plan"
    )
