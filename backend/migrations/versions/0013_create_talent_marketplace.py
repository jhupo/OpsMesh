"""create talent marketplace

Revision ID: 0013_talent_marketplace
Revises: 0012_mcp_hosted_credentials
Create Date: 2026-05-17 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_talent_marketplace"
down_revision: str | None = "0012_mcp_hosted_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "talent_listings",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.String(length=2000), nullable=False),
        sa.Column("skill_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capability_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_team_role", sa.String(length=80), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_agent_profile_id"], ["agent_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_talent_listings")),
        sa.UniqueConstraint(
            "source_agent_profile_id",
            "version",
            name="uq_talent_listings_agent_version",
        ),
    )
    op.create_index("ix_talent_listings_status_role", "talent_listings", ["status", "role"])
    op.create_index(
        "ix_talent_listings_owner_status",
        "talent_listings",
        ["owner_user_id", "status"],
    )
    op.create_table(
        "workspace_agent_installs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("talent_listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installed_agent_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hired_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["hired_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["installed_agent_profile_id"], ["agent_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_agent_profile_id"], ["agent_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["talent_listing_id"], ["talent_listings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_agent_installs")),
        sa.UniqueConstraint(
            "workspace_id",
            "talent_listing_id",
            name="uq_workspace_agent_installs_listing",
        ),
    )
    op.create_index(
        "ix_workspace_agent_installs_workspace_status",
        "workspace_agent_installs",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_agent_installs_workspace_status",
        table_name="workspace_agent_installs",
    )
    op.drop_table("workspace_agent_installs")
    op.drop_index("ix_talent_listings_owner_status", table_name="talent_listings")
    op.drop_index("ix_talent_listings_status_role", table_name="talent_listings")
    op.drop_table("talent_listings")
