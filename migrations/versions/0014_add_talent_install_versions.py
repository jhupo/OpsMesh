"""add talent install versions

Revision ID: 0014_talent_install_versions
Revises: 0013_talent_marketplace
Create Date: 2026-05-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_talent_install_versions"
down_revision: str | None = "0013_talent_marketplace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_agent_installs",
        sa.Column("current_talent_listing_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "workspace_agent_installs",
        sa.Column("installed_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "workspace_agent_installs",
        sa.Column("pinned_version", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_workspace_agent_installs_current_talent_listing_id_talent_listings"),
        "workspace_agent_installs",
        "talent_listings",
        ["current_talent_listing_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("UPDATE workspace_agent_installs SET current_talent_listing_id = talent_listing_id")
    op.alter_column("workspace_agent_installs", "installed_version", server_default=None)
    op.alter_column("workspace_agent_installs", "pinned_version", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_workspace_agent_installs_current_talent_listing_id_talent_listings"),
        "workspace_agent_installs",
        type_="foreignkey",
    )
    op.drop_column("workspace_agent_installs", "pinned_version")
    op.drop_column("workspace_agent_installs", "installed_version")
    op.drop_column("workspace_agent_installs", "current_talent_listing_id")
