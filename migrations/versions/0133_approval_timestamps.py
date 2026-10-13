"""Preserve approval instants across native PostgreSQL execution boundaries."""

import sqlalchemy as sa
from alembic import op

revision = "0133_approval_timestamps"
down_revision = "0132_webhook_execution_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The prior release cast aware client timestamps to the database wall clock.
    # Migrate with the database timezone used by that release, as for audit events.
    for name, nullable in (("created_at", False), ("decided_at", True)):
        op.alter_column(
            "approvals",
            name,
            existing_type=sa.DateTime(timezone=False),
            type_=sa.DateTime(timezone=True),
            existing_nullable=nullable,
            postgresql_using=f"{name} AT TIME ZONE current_setting('TimeZone')",
        )


def downgrade() -> None:
    for name, nullable in (("created_at", False), ("decided_at", True)):
        op.alter_column(
            "approvals",
            name,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(timezone=False),
            existing_nullable=nullable,
            postgresql_using=f"{name} AT TIME ZONE current_setting('TimeZone')",
        )
