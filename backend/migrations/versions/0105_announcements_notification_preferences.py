"""Add platform announcements and user notification preferences."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0105_announcements_notification_preferences"
down_revision = "0104_project_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_notifications",
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_workspace_notifications_recipient_user",
        "workspace_notifications",
        "users",
        ["recipient_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_workspace_notifications_workspace_recipient_created",
        "workspace_notifications",
        ["workspace_id", "recipient_user_id", "created_at"],
    )

    op.create_table(
        "platform_announcements",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("audience", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_announcements")),
    )
    op.create_index(
        "ix_platform_announcements_status_published",
        "platform_announcements",
        ["status", "published_at"],
    )
    op.create_index(
        "ix_platform_announcements_created_by",
        "platform_announcements",
        ["created_by_user_id"],
    )

    op.create_table(
        "platform_announcement_recipients",
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["announcement_id"],
            ["platform_announcements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["workspace_notifications.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_announcement_recipients")),
        sa.UniqueConstraint(
            "announcement_id",
            "workspace_id",
            "user_id",
            name="uq_platform_announcement_recipient",
        ),
    )
    op.create_index(
        "ix_platform_announcement_recipients_announcement",
        "platform_announcement_recipients",
        ["announcement_id"],
    )
    op.create_index(
        "ix_platform_announcement_recipients_workspace_user",
        "platform_announcement_recipients",
        ["workspace_id", "user_id"],
    )

    op.create_table(
        "user_notification_preferences",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("announcement_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("task_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("approval_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("security_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_notification_preferences")),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_user_notification_preferences_scope",
        ),
    )
    op.create_index(
        "ix_user_notification_preferences_user",
        "user_notification_preferences",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_notification_preferences_user", table_name="user_notification_preferences")
    op.drop_table("user_notification_preferences")
    op.drop_index(
        "ix_platform_announcement_recipients_workspace_user",
        table_name="platform_announcement_recipients",
    )
    op.drop_index(
        "ix_platform_announcement_recipients_announcement",
        table_name="platform_announcement_recipients",
    )
    op.drop_table("platform_announcement_recipients")
    op.drop_index("ix_platform_announcements_created_by", table_name="platform_announcements")
    op.drop_index(
        "ix_platform_announcements_status_published",
        table_name="platform_announcements",
    )
    op.drop_table("platform_announcements")
    op.drop_index(
        "ix_workspace_notifications_workspace_recipient_created",
        table_name="workspace_notifications",
    )
    op.drop_constraint(
        "fk_workspace_notifications_recipient_user",
        "workspace_notifications",
        type_="foreignkey",
    )
    op.drop_column("workspace_notifications", "recipient_user_id")
