"""create file access events

Revision ID: 0005_create_file_access_events
Revises: 0004_create_runtimes
Create Date: 2026-05-17 05:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_create_file_access_events"
down_revision: str | None = "0004_create_runtimes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_access_events",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_file_id"], ["workspace_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_access_events")),
    )
    op.create_index(
        "ix_file_access_events_workspace_file",
        "file_access_events",
        ["workspace_id", "workspace_file_id"],
    )
    op.create_index(
        "ix_file_access_events_workspace_user",
        "file_access_events",
        ["workspace_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_file_access_events_workspace_user", table_name="file_access_events")
    op.drop_index("ix_file_access_events_workspace_file", table_name="file_access_events")
    op.drop_table("file_access_events")

