"""create unified marketplace

Revision ID: 0054_unified_marketplace
Revises: 0053_merge_heads
Create Date: 2026-06-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_unified_marketplace"
down_revision: str | None = "0053_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "marketplace_listings",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("listing_type", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), server_default="private", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.String(length=2000), server_default="", nullable=False),
        sa.Column("version", sa.String(length=64), server_default="1.0.0", nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("install_count", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketplace_listings")),
    )
    op.create_index(
        "ix_marketplace_listings_type_visibility_status",
        "marketplace_listings",
        ["listing_type", "visibility", "status"],
    )
    op.create_index(
        "ix_marketplace_listings_workspace_type",
        "marketplace_listings",
        ["workspace_id", "listing_type"],
    )
    op.create_index(
        "ix_marketplace_listings_owner_status",
        "marketplace_listings",
        ["owner_user_id", "status"],
    )
    op.create_table(
        "workspace_marketplace_installs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("marketplace_listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installed_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("listing_type", sa.String(length=32), nullable=False),
        sa.Column("installed_name", sa.String(length=160), nullable=False),
        sa.Column("installed_version", sa.String(length=64), nullable=False),
        sa.Column("installed_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
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
        sa.ForeignKeyConstraint(["installed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["marketplace_listing_id"],
            ["marketplace_listings.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_marketplace_installs")),
        sa.UniqueConstraint(
            "workspace_id",
            "marketplace_listing_id",
            name="uq_workspace_marketplace_installs_listing",
        ),
    )
    op.create_index(
        "ix_workspace_marketplace_installs_workspace_type",
        "workspace_marketplace_installs",
        ["workspace_id", "listing_type"],
    )
    op.create_index(
        "ix_workspace_marketplace_installs_workspace_status",
        "workspace_marketplace_installs",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_marketplace_installs_workspace_status",
        table_name="workspace_marketplace_installs",
    )
    op.drop_index(
        "ix_workspace_marketplace_installs_workspace_type",
        table_name="workspace_marketplace_installs",
    )
    op.drop_table("workspace_marketplace_installs")
    op.drop_index("ix_marketplace_listings_owner_status", table_name="marketplace_listings")
    op.drop_index("ix_marketplace_listings_workspace_type", table_name="marketplace_listings")
    op.drop_index(
        "ix_marketplace_listings_type_visibility_status",
        table_name="marketplace_listings",
    )
    op.drop_table("marketplace_listings")
