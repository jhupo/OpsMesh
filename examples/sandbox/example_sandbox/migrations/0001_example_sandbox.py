"""Create conversation indexes and native SQLAlchemySession tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001_example_sandbox"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Match the installed SDK's SQLAlchemySession schema."""
    op.create_table(
        "example_sandbox_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(512), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "deleting", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_sandbox_owner_updated",
        "example_sandbox_conversations",
        ["owner_id", "updated_at"],
    )
    op.create_table(
        "example_sandbox_sdk_sessions",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "example_sandbox_sdk_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey(
                "example_sandbox_sdk_sessions.session_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("message_data", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "idx_example_sandbox_sdk_messages_session_time",
        "example_sandbox_sdk_messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    """Remove plugin-owned history and indexes."""
    op.drop_table("example_sandbox_sdk_messages")
    op.drop_table("example_sandbox_sdk_sessions")
    op.drop_table("example_sandbox_conversations")
