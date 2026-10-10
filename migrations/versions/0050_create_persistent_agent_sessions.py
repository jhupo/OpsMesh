"""create persistent agent sessions

Revision ID: 0050_persistent_agent_sessions
Revises: 0049_user_password_hash
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_persistent_agent_sessions"
down_revision: str | None = "0049_user_password_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    json_type = postgresql.JSONB(astext_type=sa.Text()) if dialect_name == "postgresql" else sa.JSON()
    json_default = sa.text("'{}'::jsonb") if dialect_name == "postgresql" else sa.text("'{}'")
    uuid_type = postgresql.UUID(as_uuid=True) if dialect_name == "postgresql" else sa.Uuid()

    op.create_table(
        "persistent_agent_sessions",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("session_key", sa.String(length=240), nullable=False),
        sa.Column("scope_type", sa.String(length=80), nullable=False),
        sa.Column("scope_id", sa.String(length=240), nullable=False),
        sa.Column("agent_profile_id", uuid_type, nullable=True),
        sa.Column("agent_team_id", uuid_type, nullable=True),
        sa.Column("task_id", uuid_type, nullable=True),
        sa.Column("openai_conversation_id", sa.String(length=240), nullable=True),
        sa.Column("metadata", json_type, nullable=False, server_default=json_default),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_team_id"], ["agent_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persistent_agent_sessions")),
        sa.UniqueConstraint(
            "workspace_id",
            "session_key",
            name="uq_agent_sessions_workspace_key",
        ),
    )
    op.create_index(
        "ix_agent_sessions_workspace_scope",
        "persistent_agent_sessions",
        ["workspace_id", "scope_type", "scope_id"],
    )
    op.create_index(
        "ix_agent_sessions_workspace_updated",
        "persistent_agent_sessions",
        ["workspace_id", "updated_at"],
    )

    op.create_table(
        "persistent_agent_session_items",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("persistent_session_id", uuid_type, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("item", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(
            ["persistent_session_id"],
            ["persistent_agent_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persistent_agent_session_items")),
        sa.UniqueConstraint(
            "persistent_session_id",
            "sequence",
            name="uq_agent_session_items_session_sequence",
        ),
    )
    op.create_index(
        "ix_agent_session_items_workspace_session",
        "persistent_agent_session_items",
        ["workspace_id", "persistent_session_id"],
    )
    op.alter_column("persistent_agent_sessions", "metadata", server_default=None)
    op.alter_column("persistent_agent_sessions", "status", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_agent_session_items_workspace_session",
        table_name="persistent_agent_session_items",
    )
    op.drop_table("persistent_agent_session_items")
    op.drop_index("ix_agent_sessions_workspace_updated", table_name="persistent_agent_sessions")
    op.drop_index("ix_agent_sessions_workspace_scope", table_name="persistent_agent_sessions")
    op.drop_table("persistent_agent_sessions")
