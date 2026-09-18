"""Durable business trigger configuration and inbox.

Revision ID: 0095_business_automations
Revises: 0094_workflow_editor_metadata
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0095_business_automations"
down_revision = "0094_workflow_editor_metadata"
branch_labels = None
depends_on = None


def _identity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "automations",
        *_identity(),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_automations_due", "automations", ["status", "next_due_at"])
    op.create_table(
        "automation_events",
        *_identity(),
        sa.Column(
            "automation_id",
            sa.Uuid(),
            sa.ForeignKey("automations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_event_id", sa.String(160), nullable=False),
        sa.Column("conversation_id", sa.String(160), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column(
            "reply_delivery_id",
            sa.Uuid(),
            sa.ForeignKey("webhook_delivery_attempts.id", ondelete="SET NULL"),
        ),
        sa.Column("error_code", sa.String(80)),
        sa.UniqueConstraint("automation_id", "external_event_id", name="uq_automation_event"),
    )
    op.create_index("ix_automation_events_pending", "automation_events", ["status", "created_at"])
    op.create_index(
        "ix_automation_events_conversation",
        "automation_events",
        ["workspace_id", "automation_id", "conversation_id"],
    )


def downgrade() -> None:
    op.drop_table("automation_events")
    op.drop_table("automations")
