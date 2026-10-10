"""add capability visibility

Revision ID: 0018_capability_visibility
Revises: 0017_model_provider_credentials
Create Date: 2026-05-18 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_capability_visibility"
down_revision: str | None = "0017_model_provider_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("owner_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "skills",
        sa.Column("visibility", sa.String(length=32), server_default="public", nullable=False),
    )
    op.alter_column("skills", "visibility", server_default=None)
    op.create_foreign_key(
        op.f("fk_skills_owner_workspace_id_workspaces"),
        "skills",
        "workspaces",
        ["owner_workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "mcp_servers",
        sa.Column("visibility", sa.String(length=32), server_default="private", nullable=False),
    )
    op.alter_column("mcp_servers", "visibility", server_default=None)


def downgrade() -> None:
    op.drop_column("mcp_servers", "visibility")
    op.drop_constraint(op.f("fk_skills_owner_workspace_id_workspaces"), "skills", type_="foreignkey")
    op.drop_column("skills", "visibility")
    op.drop_column("skills", "owner_workspace_id")
