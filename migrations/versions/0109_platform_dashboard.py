"""Persist platform metric history and administrator operation intents."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0109_platform_dashboard"
down_revision = "0108_mail_user_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_metric_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sample_slot", sa.Integer(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_platform_metric_snapshots_created", "platform_metric_snapshots", ["created_at"]
    )
    op.create_table(
        "admin_operation_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_admin_operation_requests_status_created",
        "admin_operation_requests",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_operation_requests_status_created", table_name="admin_operation_requests"
    )
    op.drop_table("admin_operation_requests")
    op.drop_index("ix_platform_metric_snapshots_created", table_name="platform_metric_snapshots")
    op.drop_table("platform_metric_snapshots")
