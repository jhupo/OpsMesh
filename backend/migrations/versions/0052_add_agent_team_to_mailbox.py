"""add agent team to mailbox threads and messages

Revision ID: 0052_agent_team_mailbox
Revises: 0051_agent_profile_management
Create Date: 2026-06-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_agent_team_mailbox"
down_revision: str | None = "0051_agent_profile_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.Uuid()
    op.add_column(
        "agent_message_threads",
        sa.Column("agent_team_id", uuid_type, nullable=True),
    )
    op.add_column(
        "agent_messages",
        sa.Column("agent_team_id", uuid_type, nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_agent_message_threads_agent_team_id_agent_teams"),
        "agent_message_threads",
        "agent_teams",
        ["agent_team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_agent_messages_agent_team_id_agent_teams"),
        "agent_messages",
        "agent_teams",
        ["agent_team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE agent_message_threads AS thread
        SET agent_team_id = task.agent_team_id
        FROM tasks AS task
        WHERE thread.task_id = task.id
          AND thread.workspace_id = task.workspace_id
          AND task.agent_team_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE agent_messages AS message
        SET agent_team_id = COALESCE(thread.agent_team_id, task.agent_team_id)
        FROM agent_message_threads AS thread
        LEFT JOIN tasks AS task
          ON task.id = message.task_id
         AND task.workspace_id = message.workspace_id
        WHERE message.thread_id = thread.id
          AND message.workspace_id = thread.workspace_id
          AND COALESCE(thread.agent_team_id, task.agent_team_id) IS NOT NULL
        """
    )
    op.create_index(
        "ix_agent_message_threads_workspace_team",
        "agent_message_threads",
        ["workspace_id", "agent_team_id"],
    )
    op.create_index(
        "ix_agent_messages_workspace_team",
        "agent_messages",
        ["workspace_id", "agent_team_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_messages_workspace_team", table_name="agent_messages")
    op.drop_index("ix_agent_message_threads_workspace_team", table_name="agent_message_threads")
    op.drop_constraint(
        op.f("fk_agent_messages_agent_team_id_agent_teams"),
        "agent_messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_agent_message_threads_agent_team_id_agent_teams"),
        "agent_message_threads",
        type_="foreignkey",
    )
    op.drop_column("agent_messages", "agent_team_id")
    op.drop_column("agent_message_threads", "agent_team_id")
