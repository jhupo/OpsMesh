"""agent profile management

Revision ID: 0051_agent_profile_management
Revises: 0050_persistent_agent_sessions
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_agent_profile_management"
down_revision: str | None = "0050_persistent_agent_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    uuid_type = postgresql.UUID(as_uuid=True) if dialect_name == "postgresql" else sa.Uuid()
    json_type = postgresql.JSONB(astext_type=sa.Text()) if dialect_name == "postgresql" else sa.JSON()

    op.add_column(
        "agent_profiles",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_profiles",
        sa.Column("last_versioned_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "agent_profile_versions",
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("agent_profile_id", uuid_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", json_type, nullable=False),
        sa.Column("changed_by_user_id", uuid_type, nullable=True),
        sa.Column("change_reason", sa.String(length=1_000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", uuid_type, nullable=False),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_profile_versions")),
        sa.UniqueConstraint(
            "agent_profile_id",
            "version",
            name="uq_agent_profile_versions_profile_version",
        ),
    )
    op.create_index(
        "ix_agent_profile_versions_workspace_profile",
        "agent_profile_versions",
        ["workspace_id", "agent_profile_id"],
    )
    op.create_index(
        "ix_agent_profile_versions_workspace_created",
        "agent_profile_versions",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_profile_versions_workspace_created",
        table_name="agent_profile_versions",
    )
    op.drop_index(
        "ix_agent_profile_versions_workspace_profile",
        table_name="agent_profile_versions",
    )
    op.drop_table("agent_profile_versions")
    op.drop_column("agent_profiles", "last_versioned_at")
    op.drop_column("agent_profiles", "archived_at")
