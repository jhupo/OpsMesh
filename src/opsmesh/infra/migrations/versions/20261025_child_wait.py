"""Persist parent waits across child interactions and worker restarts."""

import sqlalchemy as sa
from alembic import op

revision = "20261025_child_wait"
down_revision = "20261024_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("waiting_child_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_tasks_waiting_child", "tasks", "tasks", ["waiting_child_id"], ["id"])
    op.create_index("ix_tasks_waiting_child_id", "tasks", ["waiting_child_id"])


def downgrade() -> None:
    raise RuntimeError("Automatic destructive downgrade is disabled; restore a database backup")
