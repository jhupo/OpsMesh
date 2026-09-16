"""Track provider consumption of durable tool approval decisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0089_track_consumed_tool_decisions"
down_revision: str | None = "0088_normalize_managed_runtime_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pending_tool_invocations",
        sa.Column("decision_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_tool_invocations", "decision_consumed_at")
