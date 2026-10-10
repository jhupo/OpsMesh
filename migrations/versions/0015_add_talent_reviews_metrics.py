"""add talent reviews metrics

Revision ID: 0015_talent_reviews_metrics
Revises: 0014_talent_install_versions
Create Date: 2026-05-18 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_talent_reviews_metrics"
down_revision: str | None = "0014_talent_install_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "talent_listings",
        sa.Column("install_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "talent_listings",
        sa.Column("upgrade_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "talent_listings",
        sa.Column("review_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "talent_listings",
        sa.Column("rating_sum", sa.Integer(), server_default="0", nullable=False),
    )
    op.alter_column("talent_listings", "install_count", server_default=None)
    op.alter_column("talent_listings", "upgrade_count", server_default=None)
    op.alter_column("talent_listings", "review_count", server_default=None)
    op.alter_column("talent_listings", "rating_sum", server_default=None)
    op.create_table(
        "talent_listing_reviews",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("talent_listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_agent_install_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["talent_listing_id"], ["talent_listings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_agent_install_id"], ["workspace_agent_installs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_talent_listing_reviews")),
        sa.UniqueConstraint(
            "workspace_id",
            "talent_listing_id",
            name="uq_talent_listing_reviews_workspace_listing",
        ),
    )
    op.create_index(
        "ix_talent_listing_reviews_listing_status",
        "talent_listing_reviews",
        ["talent_listing_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_talent_listing_reviews_listing_status",
        table_name="talent_listing_reviews",
    )
    op.drop_table("talent_listing_reviews")
    op.drop_column("talent_listings", "rating_sum")
    op.drop_column("talent_listings", "review_count")
    op.drop_column("talent_listings", "upgrade_count")
    op.drop_column("talent_listings", "install_count")
