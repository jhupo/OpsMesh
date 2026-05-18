"""add task team snapshot

Revision ID: 0020_add_task_team_snapshot
Revises: 0019_enhance_team_members
Create Date: 2026-05-19 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_add_task_team_snapshot"
down_revision: str | None = "0019_enhance_team_members"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("team_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "team_snapshot")
