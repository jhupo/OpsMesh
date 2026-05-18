"""enhance task steps work packages

Revision ID: 0022_enhance_task_steps_work_packages
Revises: 0021_add_task_project_plan
Create Date: 2026-05-19 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_enhance_task_steps_work_packages"
down_revision: str | None = "0021_add_task_project_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_steps", sa.Column("work_package_id", sa.String(length=120), nullable=True))
    op.add_column("task_steps", sa.Column("required_role", sa.String(length=120), nullable=True))
    op.add_column(
        "task_steps",
        sa.Column("required_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "task_steps",
        sa.Column("expected_artifacts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "task_steps",
        sa.Column("acceptance_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "task_steps",
        sa.Column("review_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute("UPDATE task_steps SET required_skills = '[]'::jsonb WHERE required_skills IS NULL")
    op.execute(
        "UPDATE task_steps SET expected_artifacts = '[]'::jsonb WHERE expected_artifacts IS NULL"
    )
    op.execute(
        "UPDATE task_steps SET acceptance_criteria = '[]'::jsonb WHERE acceptance_criteria IS NULL"
    )
    op.execute("UPDATE task_steps SET review_policy = '{}'::jsonb WHERE review_policy IS NULL")
    op.alter_column("task_steps", "required_skills", nullable=False)
    op.alter_column("task_steps", "expected_artifacts", nullable=False)
    op.alter_column("task_steps", "acceptance_criteria", nullable=False)
    op.alter_column("task_steps", "review_policy", nullable=False)
    op.create_index(
        "ix_task_steps_workspace_work_package",
        "task_steps",
        ["workspace_id", "work_package_id"],
    )
    op.create_index(
        "ix_task_steps_workspace_required_role",
        "task_steps",
        ["workspace_id", "required_role"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_steps_workspace_required_role", table_name="task_steps")
    op.drop_index("ix_task_steps_workspace_work_package", table_name="task_steps")
    op.drop_column("task_steps", "review_policy")
    op.drop_column("task_steps", "acceptance_criteria")
    op.drop_column("task_steps", "expected_artifacts")
    op.drop_column("task_steps", "required_skills")
    op.drop_column("task_steps", "required_role")
    op.drop_column("task_steps", "work_package_id")
