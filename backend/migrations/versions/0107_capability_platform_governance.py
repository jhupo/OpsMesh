"""Persist platform blocks for capability resources and catalogs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0107_capability_platform_governance"
down_revision = "0106_plugin_platform_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "capabilities",
    "tool_groups",
    "skills",
    "workspace_skill_installs",
    "capability_resources",
    "mcp_servers",
    "mcp_tool_allowlist",
    "marketplace_listings",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("platform_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.add_column(table, sa.Column("platform_previous_status", sa.String(32), nullable=True))


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "platform_previous_status")
        op.drop_column(table, "platform_blocked")
