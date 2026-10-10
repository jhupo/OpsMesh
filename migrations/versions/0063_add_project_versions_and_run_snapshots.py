"""add project versions and immutable run input snapshots

Revision ID: 0063_project_run_snapshots
Revises: 0062_create_workspace_projects
Create Date: 2026-09-09 12:00:00.000000
"""

import hashlib
import json
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0063_project_run_snapshots"
down_revision: str | None = "0062_create_workspace_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Project configuration backfill requires an online database connection")
    op.add_column(
        "workspace_projects",
        sa.Column("configuration_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_workspace_projects_configuration_version_positive",
        "workspace_projects",
        "configuration_version >= 1",
    )
    op.create_table(
        "workspace_project_configuration_versions",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("change_summary", sa.String(length=500), server_default="", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["workspace_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_project_configuration_versions")),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_workspace_project_configuration_versions_version_positive"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "version",
            name="uq_workspace_project_configuration_versions_project_version",
        ),
    )
    op.create_index(
        "ix_project_configuration_versions_workspace_project",
        "workspace_project_configuration_versions",
        ["workspace_id", "project_id"],
    )
    _backfill_configuration_versions()

    op.add_column(
        "workspace_project_files",
        sa.Column("supersedes_project_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "workspace_project_files",
        sa.Column("version", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY project_id, project_path
                       ORDER BY created_at ASC, id ASC
                   ) AS project_file_version
            FROM workspace_project_files
        )
        UPDATE workspace_project_files AS project_file
        SET version = ranked.project_file_version
        FROM ranked
        WHERE project_file.id = ranked.id
        """
    )
    op.alter_column("workspace_project_files", "version", nullable=False, server_default="1")
    op.create_foreign_key(
        op.f("fk_workspace_project_files_supersedes_project_file_id_workspace_project_files"),
        "workspace_project_files",
        "workspace_project_files",
        ["supersedes_project_file_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_workspace_project_files_project_path_version",
        "workspace_project_files",
        ["project_id", "project_path", "version"],
    )
    op.create_check_constraint(
        "ck_workspace_project_files_version_positive",
        "workspace_project_files",
        "version >= 1",
    )

    op.create_table(
        "agent_run_project_snapshots",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("configuration_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["workspace_projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["configuration_version_id"],
            ["workspace_project_configuration_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run_project_snapshots")),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_agent_run_project_snapshots_schema_version_positive"),
        ),
        sa.UniqueConstraint("agent_run_id", name="uq_agent_run_project_snapshots_run"),
    )
    op.create_index(
        "ix_agent_run_project_snapshots_workspace_project",
        "agent_run_project_snapshots",
        ["workspace_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_run_project_snapshots_workspace_project",
        table_name="agent_run_project_snapshots",
    )
    op.drop_table("agent_run_project_snapshots")
    op.drop_constraint(
        "ck_workspace_project_files_version_positive",
        "workspace_project_files",
        type_="check",
    )
    op.drop_constraint(
        "uq_workspace_project_files_project_path_version",
        "workspace_project_files",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_workspace_project_files_supersedes_project_file_id_workspace_project_files"),
        "workspace_project_files",
        type_="foreignkey",
    )
    op.drop_column("workspace_project_files", "version")
    op.drop_column("workspace_project_files", "supersedes_project_file_id")
    op.drop_index(
        "ix_project_configuration_versions_workspace_project",
        table_name="workspace_project_configuration_versions",
    )
    op.drop_table("workspace_project_configuration_versions")
    op.drop_constraint(
        "ck_workspace_projects_configuration_version_positive",
        "workspace_projects",
        type_="check",
    )
    op.drop_column("workspace_projects", "configuration_version")


def _backfill_configuration_versions() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, workspace_id, created_by_user_id, configuration, created_at
            FROM workspace_projects
            """
        )
    ).mappings()
    version_table = sa.table(
        "workspace_project_configuration_versions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
        sa.column("project_id", postgresql.UUID(as_uuid=True)),
        sa.column("version", sa.Integer()),
        sa.column("configuration", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("checksum_sha256", sa.String(length=64)),
        sa.column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.column("change_summary", sa.String(length=500)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    values = [
        {
            "id": uuid4(),
            "workspace_id": row["workspace_id"],
            "project_id": row["id"],
            "version": 1,
            "configuration": row["configuration"] or {},
            "checksum_sha256": sha256_json(row["configuration"] or {}),
            "created_by_user_id": row["created_by_user_id"],
            "change_summary": "Initial configuration",
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    if values:
        connection.execute(version_table.insert(), values)


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
