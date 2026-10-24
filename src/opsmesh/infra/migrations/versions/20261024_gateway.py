"""Native channel events, capabilities, durable operations and delivery receipts."""

import sqlalchemy as sa
from alembic import op

revision = "20261024_gateway"
down_revision = "20261024_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channels", sa.Column("capabilities", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column("deliveries", sa.Column("encrypted_receipt", sa.Text(), nullable=True))
    op.create_table(
        "channel_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id"), nullable=False),
        sa.Column("external_event_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("encrypted_event", sa.Text(), nullable=False),
        sa.UniqueConstraint("channel_id", "external_event_id"),
    )
    op.create_index("ix_channel_events_channel_id", "channel_events", ["channel_id"])
    op.create_table(
        "channel_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("encrypted_parameters", sa.Text(), nullable=False),
        sa.Column("encrypted_result", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("channel_id", "idempotency_key"),
    )
    op.create_index("ix_channel_operations_channel_id", "channel_operations", ["channel_id"])
    op.create_index("ix_channel_operations_status", "channel_operations", ["status"])


def downgrade() -> None:
    raise RuntimeError("Automatic destructive downgrade is disabled; restore a database backup")
