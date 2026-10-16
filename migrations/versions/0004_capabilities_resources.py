"""capability definitions, versions and packages; resource files and artifacts

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17 00:00:00.000000+00:00

S5 tables: the capability catalog (definitions, immutable versions, package
blobs) and the resource mechanism (directories, files, versions, contents,
grants, runtime policies and run/invocation artifacts). No historical
architecture migration is inherited.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capability_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capability_definitions")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_capability_definitions_created_by_user_id_users"),
        ),
        sa.UniqueConstraint(
            "type", "key", name=op.f("uq_capability_definitions_type_key")
        ),
    )
    op.create_index(
        op.f("ix_capability_definitions_type"),
        "capability_definitions",
        ["type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_capability_definitions_status"),
        "capability_definitions",
        ["status"],
        unique=False,
    )
    op.create_table(
        "capability_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("resource_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("dependencies", postgresql.JSONB(), nullable=False),
        sa.Column("flow_templates", postgresql.JSONB(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=True),
        sa.Column("package_digest", sa.String(length=64), nullable=True),
        sa.Column("package_size", sa.BigInteger(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capability_versions")),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["capability_definitions.id"],
            ondelete="RESTRICT",
            name=op.f("fk_capability_versions_definition_id_capability_definitions"),
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_capability_versions_published_by_user_id_users"),
        ),
        sa.UniqueConstraint(
            "definition_id",
            "version",
            name=op.f("uq_capability_versions_definition_id_version"),
        ),
    )
    op.create_index(
        op.f("ix_capability_versions_definition_id"),
        "capability_versions",
        ["definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_capability_versions_status"),
        "capability_versions",
        ["status"],
        unique=False,
    )
    op.create_table(
        "capability_packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=260), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capability_packages")),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["capability_versions.id"],
            ondelete="RESTRICT",
            name=op.f("fk_capability_packages_version_id_capability_versions"),
        ),
        sa.UniqueConstraint("version_id", name=op.f("uq_capability_packages_version_id")),
    )
    op.create_table(
        "resource_directories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_directories")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_directories_owner_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["resource_directories.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_directories_parent_id_resource_directories"),
        ),
        sa.UniqueConstraint(
            "owner_user_id", "path", name=op.f("uq_resource_directories_owner_user_id_path")
        ),
    )
    op.create_index(
        op.f("ix_resource_directories_owner_user_id"),
        "resource_directories",
        ["owner_user_id"],
        unique=False,
    )
    op.create_table(
        "resource_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("directory_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=260), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_files")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_files_owner_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["directory_id"],
            ["resource_directories.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_files_directory_id_resource_directories"),
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "directory_id",
            "name",
            name=op.f("uq_resource_files_owner_user_id_directory_id_name"),
        ),
    )
    op.create_index(
        op.f("ix_resource_files_owner_user_id"),
        "resource_files",
        ["owner_user_id"],
        unique=False,
    )
    op.create_table(
        "resource_file_contents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=1000), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_file_contents")),
    )
    op.create_index(
        op.f("ix_resource_file_contents_digest"),
        "resource_file_contents",
        ["digest"],
        unique=False,
    )
    op.create_table(
        "resource_file_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_file_versions")),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["resource_files.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_file_versions_file_id_resource_files"),
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["resource_file_contents.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_file_versions_content_id_resource_file_contents"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_file_versions_created_by_user_id_users"),
        ),
        sa.UniqueConstraint(
            "file_id", "version", name=op.f("uq_resource_file_versions_file_id_version")
        ),
    )
    op.create_index(
        op.f("ix_resource_file_versions_file_id"),
        "resource_file_versions",
        ["file_id"],
        unique=False,
    )
    op.create_table(
        "resource_file_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("grantee_user_id", sa.Uuid(), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_file_grants")),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["resource_files.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_file_grants_file_id_resource_files"),
        ),
        sa.ForeignKeyConstraint(
            ["grantee_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_file_grants_grantee_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_file_grants_created_by_user_id_users"),
        ),
        sa.UniqueConstraint(
            "file_id",
            "grantee_user_id",
            name=op.f("uq_resource_file_grants_file_id_grantee_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_resource_file_grants_file_id"),
        "resource_file_grants",
        ["file_id"],
        unique=False,
    )
    op.create_table(
        "resource_runtime_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("frozen_input_versions", postgresql.JSONB(), nullable=False),
        sa.Column("relative_paths", postgresql.JSONB(), nullable=False),
        sa.Column("output_declarations", postgresql.JSONB(), nullable=False),
        sa.Column("use_in_execution", sa.Boolean(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_runtime_policies")),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["resource_files.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_runtime_policies_file_id_resource_files"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_runtime_policies_updated_by_user_id_users"),
        ),
        sa.UniqueConstraint("file_id", name=op.f("uq_resource_runtime_policies_file_id")),
    )
    op.create_table(
        "resource_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=260), nullable=False),
        sa.Column("content_id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=True),
        sa.Column("storage_key", sa.String(length=1000), nullable=False),
        sa.Column("labels", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_artifacts")),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_artifacts_owner_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["resource_file_contents.id"],
            ondelete="RESTRICT",
            name=op.f("fk_resource_artifacts_content_id_resource_file_contents"),
        ),
    )
    op.create_index(
        op.f("ix_resource_artifacts_source_kind"),
        "resource_artifacts",
        ["source_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_resource_artifacts_source_id"),
        "resource_artifacts",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_resource_artifacts_owner_user_id"),
        "resource_artifacts",
        ["owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("resource_artifacts")
    op.drop_table("resource_runtime_policies")
    op.drop_table("resource_file_grants")
    op.drop_table("resource_file_versions")
    op.drop_table("resource_file_contents")
    op.drop_table("resource_files")
    op.drop_table("resource_directories")
    op.drop_table("capability_packages")
    op.drop_table("capability_versions")
    op.drop_table("capability_definitions")
