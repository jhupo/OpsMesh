"""Fence sandbox checkpoints, track explicit file edits and sequence gateway replies."""

import sqlalchemy as sa
from alembic import op

revision = "20261024_integrity"
down_revision = "20261024_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("sandbox_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_sessions", sa.Column("sandbox_plugin_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_session_sandbox_plugin", "agent_sessions", "plugins", ["sandbox_plugin_id"], ["id"]
    )
    op.add_column("agent_sessions", sa.Column("encrypted_sandbox_config", sa.Text(), nullable=True))
    op.add_column(
        "agent_sessions",
        sa.Column("sandbox_access_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("checkpoints", sa.Column("sandbox_generation", sa.Integer(), nullable=True))
    op.add_column(
        "inbox", sa.Column("reply_sequence", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute(
        "UPDATE inbox SET reply_sequence = (SELECT count(*) FROM deliveries JOIN tasks ON tasks.id = deliveries.task_id WHERE deliveries.channel_id = inbox.channel_id AND tasks.id = inbox.task_id)"
    )
    op.add_column(
        "agent_instances", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    raise RuntimeError("Automatic destructive downgrade is disabled; restore a database backup")
