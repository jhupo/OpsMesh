"""add skill install disabled timestamp

Revision ID: 0035_skill_install_disabled_at
Revises: 0034_enhance_mcp_tool_call_logs
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_skill_install_disabled_at"
down_revision: str | None = "0034_enhance_mcp_tool_call_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_skill_installs",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_skill_installs", "disabled_at")
