"""Use durable ordered task events, remove the Redis publication state machine."""

import sqlalchemy as sa
from alembic import op

revision = "0127_task_event_store"
down_revision = "0126_team_orchestration_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("task_event_outbox", "task_events")
    op.add_column("task_events", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE task_events AS event SET sequence = ordered.position
        FROM (SELECT id, row_number() OVER (
            PARTITION BY task_id ORDER BY created_at, id
        ) AS position FROM task_events) AS ordered WHERE event.id = ordered.id
    """)
    op.alter_column("task_events", "sequence", nullable=False)
    op.drop_index("ix_task_event_outbox_status_available", table_name="task_events")
    op.drop_index("ix_task_event_outbox_workspace_status", table_name="task_events")
    op.drop_index("ix_task_event_outbox_workspace_task", table_name="task_events")
    op.drop_constraint(op.f("ck_task_event_outbox_status_valid"), "task_events", type_="check")
    op.drop_constraint(
        op.f("ck_task_event_outbox_attempts_non_negative"), "task_events", type_="check"
    )
    op.drop_constraint("uq_task_event_outbox_event_id", "task_events", type_="unique")
    for column in ("status", "attempts", "available_at", "published_at", "stream_id", "last_error"):
        op.drop_column("task_events", column)
    op.create_unique_constraint("uq_task_events_event_id", "task_events", ["event_id"])
    op.create_unique_constraint(
        "uq_task_events_task_sequence", "task_events", ["task_id", "sequence"]
    )
    op.create_index("ix_task_events_workspace_task", "task_events", ["workspace_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_events_workspace_task", table_name="task_events")
    op.drop_constraint("uq_task_events_task_sequence", "task_events", type_="unique")
    op.drop_constraint("uq_task_events_event_id", "task_events", type_="unique")
    op.add_column(
        "task_events",
        sa.Column("status", sa.String(32), server_default="published", nullable=False),
    )
    op.add_column(
        "task_events", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "task_events",
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("task_events", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column("task_events", sa.Column("stream_id", sa.String(80)))
    op.add_column("task_events", sa.Column("last_error", sa.String()))
    op.execute("UPDATE task_events SET published_at = created_at")
    op.drop_column("task_events", "sequence")
    op.rename_table("task_events", "task_event_outbox")
    op.create_check_constraint(
        "status_valid",
        "task_event_outbox",
        "status in ('pending', 'publishing', 'published', 'failed')",
    )
    op.create_check_constraint("attempts_non_negative", "task_event_outbox", "attempts >= 0")
    op.create_unique_constraint("uq_task_event_outbox_event_id", "task_event_outbox", ["event_id"])
    op.create_index(
        "ix_task_event_outbox_status_available", "task_event_outbox", ["status", "available_at"]
    )
    op.create_index(
        "ix_task_event_outbox_workspace_status", "task_event_outbox", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_task_event_outbox_workspace_task", "task_event_outbox", ["workspace_id", "task_id"]
    )
