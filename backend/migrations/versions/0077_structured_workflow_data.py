"""Persist structured task-step output for workflow data bindings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077_structured_workflow_data"
down_revision = "0076_subworkflow_invocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_steps",
        sa.Column("result_payload", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_steps", "result_payload")
