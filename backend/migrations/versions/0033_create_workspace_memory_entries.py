"""create workspace memory entries

Revision ID: 0033_workspace_memory_entries
Revises: 0032_self_hosted_mcp_jobs
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_workspace_memory_entries"
down_revision: str | None = "0032_self_hosted_mcp_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_memory_entries",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=True),
        sa.Column("source_id", sa.String(length=120), nullable=True),
        sa.Column("entry_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visibility_scope", sa.String(length=32), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_agent_profile_id"],
            ["agent_profiles.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_agent_run_id"],
            ["agent_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_memory_entries_workspace_scope",
        "workspace_memory_entries",
        ["workspace_id", "visibility_scope"],
    )
    op.create_index(
        "ix_workspace_memory_entries_workspace_source",
        "workspace_memory_entries",
        ["workspace_id", "source_type"],
    )
    op.create_index(
        "ix_workspace_memory_entries_workspace_status",
        "workspace_memory_entries",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_workspace_memory_entries_workspace_type",
        "workspace_memory_entries",
        ["workspace_id", "entry_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_memory_entries_workspace_type",
        table_name="workspace_memory_entries",
    )
    op.drop_index(
        "ix_workspace_memory_entries_workspace_status",
        table_name="workspace_memory_entries",
    )
    op.drop_index(
        "ix_workspace_memory_entries_workspace_source",
        table_name="workspace_memory_entries",
    )
    op.drop_index(
        "ix_workspace_memory_entries_workspace_scope",
        table_name="workspace_memory_entries",
    )
    op.drop_table("workspace_memory_entries")
