"""create webhook delivery tables

Revision ID: 0043_webhook_delivery
Revises: 0042_task_event_outbox
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_webhook_delivery"
down_revision: str | None = "0042_task_event_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("target_url", sa.String(length=1024), nullable=False),
        sa.Column("event_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("encrypted_signing_secret", sa.String(), nullable=False),
        sa.Column("signing_secret_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_message", sa.String(length=1000), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status in ('active', 'disabled')",
            name=op.f("ck_webhook_subscriptions_status_valid"),
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_subscriptions")),
    )
    op.create_index(
        "ix_webhook_subscriptions_workspace_created",
        "webhook_subscriptions",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_webhook_subscriptions_workspace_status",
        "webhook_subscriptions",
        ["workspace_id", "status"],
    )

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body_snippet", sa.String(length=1000), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dead_letter_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'queued', 'delivering', 'succeeded', 'retrying', 'dead_lettered')",
            name=op.f("ck_webhook_delivery_attempts_status_valid"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_webhook_delivery_attempts_attempt_count_non_negative"),
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name=op.f("ck_webhook_delivery_attempts_max_attempts_positive"),
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["webhook_subscriptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_delivery_attempts")),
    )
    op.create_index(
        "ix_webhook_delivery_attempts_due",
        "webhook_delivery_attempts",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_subscription_created",
        "webhook_delivery_attempts",
        ["subscription_id", "created_at"],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_workspace_event",
        "webhook_delivery_attempts",
        ["workspace_id", "event_type", "event_id"],
    )
    op.create_index(
        "ix_webhook_delivery_attempts_workspace_status",
        "webhook_delivery_attempts",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_delivery_attempts_workspace_status", table_name="webhook_delivery_attempts")
    op.drop_index("ix_webhook_delivery_attempts_workspace_event", table_name="webhook_delivery_attempts")
    op.drop_index("ix_webhook_delivery_attempts_subscription_created", table_name="webhook_delivery_attempts")
    op.drop_index("ix_webhook_delivery_attempts_due", table_name="webhook_delivery_attempts")
    op.drop_table("webhook_delivery_attempts")
    op.drop_index("ix_webhook_subscriptions_workspace_status", table_name="webhook_subscriptions")
    op.drop_index("ix_webhook_subscriptions_workspace_created", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
