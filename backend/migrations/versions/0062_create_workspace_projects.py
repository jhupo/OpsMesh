"""create workspace project layouts

Revision ID: 0062_create_workspace_projects
Revises: 0061_pending_tool_execution_results
Create Date: 2026-09-09 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0062_create_workspace_projects"
down_revision: str | None = "0061_pending_tool_execution_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_projects",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=2_000), server_default="", nullable=False),
        sa.Column("input_path", sa.String(length=512), server_default="inputs", nullable=False),
        sa.Column("work_path", sa.String(length=512), server_default="work", nullable=False),
        sa.Column("output_path", sa.String(length=512), server_default="outputs", nullable=False),
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_projects")),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_workspace_projects_workspace_slug"),
    )
    op.create_index(
        "ix_workspace_projects_workspace_status",
        "workspace_projects",
        ["workspace_id", "status"],
    )
    op.create_table(
        "workspace_project_files",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_path", sa.String(length=512), nullable=False),
        sa.Column("access_mode", sa.String(length=32), server_default="read_only", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["workspace_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_file_id"], ["workspace_files.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_project_files")),
    )
    op.create_index(
        "ix_project_files_workspace_project",
        "workspace_project_files",
        ["workspace_id", "project_id"],
    )
    op.create_index(
        "uq_project_files_active_project_path",
        "workspace_project_files",
        ["project_id", "project_path"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_project_files_active_project_file",
        "workspace_project_files",
        ["project_id", "workspace_file_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "workspace_project_outputs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_path", sa.String(length=512), nullable=False),
        sa.Column("artifact_type", sa.String(length=80), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("max_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["workspace_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_project_outputs")),
    )
    op.create_index(
        "ix_project_outputs_workspace_project",
        "workspace_project_outputs",
        ["workspace_id", "project_id"],
    )
    op.create_index(
        "uq_project_outputs_active_project_path",
        "workspace_project_outputs",
        ["project_id", "project_path"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.add_column(
        "tasks", sa.Column("workspace_project_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_tasks_workspace_project_id_workspace_projects",
        "tasks",
        "workspace_projects",
        ["workspace_project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tasks_workspace_project", "tasks", ["workspace_id", "workspace_project_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_workspace_project", table_name="tasks")
    op.drop_constraint(
        "fk_tasks_workspace_project_id_workspace_projects", "tasks", type_="foreignkey"
    )
    op.drop_column("tasks", "workspace_project_id")
    op.drop_index("uq_project_outputs_active_project_path", table_name="workspace_project_outputs")
    op.drop_index("ix_project_outputs_workspace_project", table_name="workspace_project_outputs")
    op.drop_table("workspace_project_outputs")
    op.drop_index("uq_project_files_active_project_file", table_name="workspace_project_files")
    op.drop_index("uq_project_files_active_project_path", table_name="workspace_project_files")
    op.drop_index("ix_project_files_workspace_project", table_name="workspace_project_files")
    op.drop_table("workspace_project_files")
    op.drop_index("ix_workspace_projects_workspace_status", table_name="workspace_projects")
    op.drop_table("workspace_projects")
