"""Persist platform authority over plugin installations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0106_plugin_platform_governance"
down_revision = "0105_announcements_notification_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plugin_installs",
        sa.Column("platform_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("plugin_installs", "platform_blocked")
