"""create workspace notification center

Revision ID: 0044_notification_center
Revises: 0043_webhook_delivery
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_notification_center"
down_revision: str | None = "0043_webhook_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_notifications",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_notifications")),
    )
    op.create_index(
        "ix_workspace_notifications_workspace_archived",
        "workspace_notifications",
        ["workspace_id", "archived_at"],
    )
    op.create_index(
        "ix_workspace_notifications_workspace_created",
        "workspace_notifications",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_notifications_workspace_read",
        "workspace_notifications",
        ["workspace_id", "read_at"],
    )
    op.create_index(
        "ix_workspace_notifications_workspace_severity",
        "workspace_notifications",
        ["workspace_id", "severity"],
    )
    op.create_index(
        "ix_workspace_notifications_workspace_source",
        "workspace_notifications",
        ["workspace_id", "source_type", "source_id"],
    )
    op.create_index(
        "ix_workspace_notifications_workspace_type",
        "workspace_notifications",
        ["workspace_id", "notification_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_notifications_workspace_type", table_name="workspace_notifications")
    op.drop_index("ix_workspace_notifications_workspace_source", table_name="workspace_notifications")
    op.drop_index("ix_workspace_notifications_workspace_severity", table_name="workspace_notifications")
    op.drop_index("ix_workspace_notifications_workspace_read", table_name="workspace_notifications")
    op.drop_index("ix_workspace_notifications_workspace_created", table_name="workspace_notifications")
    op.drop_index("ix_workspace_notifications_workspace_archived", table_name="workspace_notifications")
    op.drop_table("workspace_notifications")
