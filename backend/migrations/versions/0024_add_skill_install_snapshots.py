"""add skill install snapshots

Revision ID: 0024_skill_install_snapshots
Revises: 0023_create_task_messages
Create Date: 2026-05-19 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_skill_install_snapshots"
down_revision: str | None = "0023_create_task_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_skill_installs",
        sa.Column("installed_key", sa.String(length=120), server_default="", nullable=False),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column("installed_name", sa.String(length=160), server_default="", nullable=False),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column("installed_version", sa.String(length=80), server_default="", nullable=False),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column("installed_description", sa.String(), server_default="", nullable=False),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column(
            "installed_capability_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column(
            "installed_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column("source_owner_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column("source_visibility", sa.String(length=32), server_default="public", nullable=False),
    )
    op.add_column(
        "workspace_skill_installs",
        sa.Column("source_checksum", sa.String(length=128), server_default="", nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_workspace_skill_installs_source_owner_workspace_id_workspaces"),
        "workspace_skill_installs",
        "workspaces",
        ["source_owner_workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE workspace_skill_installs AS install
        SET
            installed_key = skill.key,
            installed_name = skill.name,
            installed_version = skill.version,
            installed_description = skill.description,
            installed_capability_keys = skill.capability_keys,
            installed_manifest = skill.manifest,
            source_owner_workspace_id = skill.owner_workspace_id,
            source_visibility = skill.visibility,
            source_checksum = ''
        FROM skills AS skill
        WHERE install.skill_id = skill.id
        """
    )
    for column_name in (
        "installed_key",
        "installed_name",
        "installed_version",
        "installed_description",
        "installed_capability_keys",
        "installed_manifest",
        "source_visibility",
        "source_checksum",
    ):
        op.alter_column("workspace_skill_installs", column_name, server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_workspace_skill_installs_source_owner_workspace_id_workspaces"),
        "workspace_skill_installs",
        type_="foreignkey",
    )
    op.drop_column("workspace_skill_installs", "source_checksum")
    op.drop_column("workspace_skill_installs", "source_visibility")
    op.drop_column("workspace_skill_installs", "source_owner_workspace_id")
    op.drop_column("workspace_skill_installs", "installed_manifest")
    op.drop_column("workspace_skill_installs", "installed_capability_keys")
    op.drop_column("workspace_skill_installs", "installed_description")
    op.drop_column("workspace_skill_installs", "installed_version")
    op.drop_column("workspace_skill_installs", "installed_name")
    op.drop_column("workspace_skill_installs", "installed_key")
