"""add artifact work package metadata

Revision ID: 0031_artifact_work_package_metadata
Revises: 0030_task_planning_attempts
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_artifact_work_package_metadata"
down_revision: str | None = "0030_task_planning_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("task_step_id", sa.UUID(), nullable=True))
    op.add_column("artifacts", sa.Column("agent_profile_id", sa.UUID(), nullable=True))
    op.add_column("artifacts", sa.Column("supersedes_artifact_id", sa.UUID(), nullable=True))
    op.add_column("artifacts", sa.Column("work_package_id", sa.String(length=120), nullable=True))
    op.add_column(
        "artifacts",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "artifacts",
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="pending"),
    )
    op.create_foreign_key(
        op.f("fk_artifacts_task_step_id_task_steps"),
        "artifacts",
        "task_steps",
        ["task_step_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_artifacts_agent_profile_id_agent_profiles"),
        "artifacts",
        "agent_profiles",
        ["agent_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_artifacts_supersedes_artifact_id_artifacts"),
        "artifacts",
        "artifacts",
        ["supersedes_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_artifacts_workspace_work_package",
        "artifacts",
        ["workspace_id", "work_package_id"],
    )
    op.alter_column("artifacts", "version", server_default=None)
    op.alter_column("artifacts", "review_status", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_artifacts_workspace_work_package", table_name="artifacts")
    op.drop_constraint(
        op.f("fk_artifacts_supersedes_artifact_id_artifacts"),
        "artifacts",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_artifacts_agent_profile_id_agent_profiles"),
        "artifacts",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_artifacts_task_step_id_task_steps"),
        "artifacts",
        type_="foreignkey",
    )
    op.drop_column("artifacts", "review_status")
    op.drop_column("artifacts", "version")
    op.drop_column("artifacts", "work_package_id")
    op.drop_column("artifacts", "supersedes_artifact_id")
    op.drop_column("artifacts", "agent_profile_id")
    op.drop_column("artifacts", "task_step_id")
