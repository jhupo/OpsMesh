"""add task project plan

Revision ID: 0021_add_task_project_plan
Revises: 0020_add_task_team_snapshot
Create Date: 2026-05-19 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_add_task_project_plan"
down_revision: str | None = "0020_add_task_team_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("project_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "project_plan")
