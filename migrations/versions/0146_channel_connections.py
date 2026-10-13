"""Remote channel ingress and externally acknowledged reply delivery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0146_channel_connections"
down_revision = "0145_stable_plugin_installations"
branch_labels = None
depends_on = None


def _identity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM automation_events WHERE reply_delivery_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Finish or explicitly reconcile legacy Automation webhook deliveries before upgrading';
        END IF;
    END $$""")
    op.create_table(
        "channel_connections",
        *_identity(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("install_id", sa.Uuid(), nullable=False),
        sa.Column("capability_key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("account_id", sa.String(160), nullable=False),
        sa.Column("endpoint_url", sa.String(2048), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_signing_secret", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_channel_connection_scope"),
        sa.UniqueConstraint(
            "workspace_id", "install_id", "name", name="uq_channel_connection_name"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "install_id"], ["plugin_installs.workspace_id", "plugin_installs.id"]
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="channel_connection_status"),
        sa.CheckConstraint("configuration_revision > 0", name="channel_connection_revision"),
    )
    op.create_table(
        "channel_ingress",
        *_identity(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(260), nullable=False),
        sa.Column("external_id", sa.String(160), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("automation_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "source",
            "external_id",
            name="uq_channel_ingress_event",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["channel_connections.workspace_id", "channel_connections.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "automation_id"], ["automations.workspace_id", "automations.id"]
        ),
    )
    op.create_table(
        "channel_deliveries",
        *_identity(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("connection_revision", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider_message_id", sa.String(260)),
        sa.Column("error_code", sa.String(120)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "workspace_id", "event_id", "sequence", name="uq_channel_delivery_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["channel_connections.workspace_id", "channel_connections.id"],
        ),
        sa.CheckConstraint(
            "status IN ('pending','sending','delivered','failed','unknown')",
            name="channel_delivery_status",
        ),
    )
    op.drop_constraint(
        op.f("fk_automation_events_reply_delivery_id_webhook_delivery_attempts"),
        "automation_events",
        type_="foreignkey",
    )
    op.add_column("automation_events", sa.Column("source_connection_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_automation_event_channel_scope",
        "automation_events",
        "channel_connections",
        ["workspace_id", "source_connection_id"],
        ["workspace_id", "id"],
    )
    op.drop_constraint("uq_automation_event", "automation_events", type_="unique")
    op.create_unique_constraint(
        "uq_automation_event",
        "automation_events",
        ["workspace_id", "automation_id", "source_connection_id", "external_event_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_foreign_key(
        "fk_automation_events_reply_delivery_id_channel_deliveries",
        "automation_events",
        "channel_deliveries",
        ["reply_delivery_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Retained event snapshots remain unchanged; administrators explicitly reconfigure message routes.
    op.execute(
        "UPDATE automations SET status = 'paused', configuration = configuration - 'reply_subscription_id'"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM channel_connections) THEN
            RAISE EXCEPTION 'Remove channel connections before downgrading; conversion would lose routing and receipt evidence';
        END IF;
    END $$""")
    op.drop_constraint(
        "fk_automation_events_reply_delivery_id_channel_deliveries",
        "automation_events",
        type_="foreignkey",
    )
    op.drop_constraint("uq_automation_event", "automation_events", type_="unique")
    op.create_unique_constraint(
        "uq_automation_event", "automation_events", ["automation_id", "external_event_id"]
    )
    op.drop_constraint("fk_automation_event_channel_scope", "automation_events", type_="foreignkey")
    op.drop_column("automation_events", "source_connection_id")
    op.create_foreign_key(
        op.f("fk_automation_events_reply_delivery_id_webhook_delivery_attempts"),
        "automation_events",
        "webhook_delivery_attempts",
        ["reply_delivery_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_table("channel_deliveries")
    op.drop_table("channel_ingress")
    op.drop_table("channel_connections")
