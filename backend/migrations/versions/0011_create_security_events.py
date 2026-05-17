"""create security events

Revision ID: 0011_security_events
Revises: 0010_operations
Create Date: 2026-05-17 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_security_events"
down_revision: str | None = "0010_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("source_ip", sa.String(length=80), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("request_id", sa.String(length=80), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_events")),
    )
    op.create_index(
        "ix_security_events_workspace_action",
        "security_events",
        ["workspace_id", "action"],
    )
    op.create_index("ix_security_events_user_action", "security_events", ["user_id", "action"])
    op.create_index("ix_security_events_severity", "security_events", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_security_events_severity", table_name="security_events")
    op.drop_index("ix_security_events_user_action", table_name="security_events")
    op.drop_index("ix_security_events_workspace_action", table_name="security_events")
    op.drop_table("security_events")
