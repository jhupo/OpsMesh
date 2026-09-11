"""Persist runtime command denial evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0080_runtime_command_security_state"
down_revision = "0079_per_run_runtime_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_commands",
        sa.Column("error", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runtime_commands", "error")
