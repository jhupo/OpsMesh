"""Persist completion of execution cleanup separately from product cancellation."""

import sqlalchemy as sa
from alembic import op

revision = "0143_execution_release_boundary"
down_revision = "0142_openai_execution_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("execution_released_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE agent_runs SET execution_released_at = completed_at "
        "WHERE status IN ('completed', 'failed', 'cancelled')"
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "execution_released_at")
