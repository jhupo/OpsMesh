"""Persist ordered task events for resumable SSE subscriptions."""

import sqlalchemy as sa
from alembic import op

revision = "20261024_events"
down_revision = "20261024_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("event_sequence", "event_bytes"):
        op.add_column("tasks", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "task_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("encrypted_data", sa.Text(), nullable=False),
        sa.UniqueConstraint("task_id", "sequence"),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])


def downgrade() -> None:
    raise RuntimeError("Automatic destructive downgrade is disabled; restore a database backup")
