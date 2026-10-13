"""Replace the retired webhook queue job reference with a native execution identity."""

import sqlalchemy as sa
from alembic import op

revision = "0132_webhook_execution_reference"
down_revision = "0131_retired_agent_tool_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("webhook_delivery_attempts", "job_id")
    op.add_column("webhook_delivery_attempts", sa.Column("execution_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("webhook_delivery_attempts", "execution_id")
    op.add_column("webhook_delivery_attempts", sa.Column("job_id", sa.Uuid(), nullable=True))
