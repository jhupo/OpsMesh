"""add audit event hash chain

Revision ID: 0040_audit_event_hash_chain
Revises: 0039_db_check_constraints
Create Date: 2026-06-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_audit_event_hash_chain"
down_revision: str | None = "0039_db_check_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.add_column(sa.Column("previous_hash", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("current_hash", sa.String(length=80), nullable=True))
        batch_op.create_index(
            "ix_audit_events_workspace_created",
            ["workspace_id", "created_at"],
        )
        batch_op.create_index(
            "ix_audit_events_workspace_current_hash",
            ["workspace_id", "current_hash"],
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_index("ix_audit_events_workspace_current_hash")
        batch_op.drop_index("ix_audit_events_workspace_created")
        batch_op.drop_column("current_hash")
        batch_op.drop_column("previous_hash")
