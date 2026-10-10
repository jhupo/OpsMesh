"""Message continuation, control results and fair inbox polling.

Revision ID: 0097_message_collaboration
Revises: 0096_remote_plugin_lifecycle
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0097_message_collaboration"
down_revision = "0096_remote_plugin_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_events",
        sa.Column("result_payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.alter_column("automation_events", "result_payload", server_default=None)
    op.add_column("automation_events", sa.Column("progress_fingerprint", sa.String(64)))
    op.add_column(
        "automation_events",
        sa.Column("notification_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("automation_events", "notification_sequence", server_default=None)
    op.add_column("automation_events", sa.Column("checked_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_automation_events_scan", "automation_events", ["status", "checked_at", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_automation_events_scan", table_name="automation_events")
    op.drop_column("automation_events", "notification_sequence")
    op.drop_column("automation_events", "checked_at")
    op.drop_column("automation_events", "progress_fingerprint")
    op.drop_column("automation_events", "result_payload")
