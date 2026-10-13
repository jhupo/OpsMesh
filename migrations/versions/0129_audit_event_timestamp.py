"""Preserve audit timestamps across PostgreSQL timezone conversion."""

import sqlalchemy as sa
from alembic import op

revision = "0129_audit_event_timestamp"
down_revision = "0128_native_temporal_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The previous column stored the database's wall clock after converting an
    # aware client timestamp. Recover the original instant without rehashing WORM
    # evidence. Run with the same database timezone used by the previous release.
    op.alter_column(
        "audit_events",
        "created_at",
        existing_type=sa.DateTime(timezone=False),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE current_setting('TimeZone')",
    )


def downgrade() -> None:
    op.alter_column(
        "audit_events",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(timezone=False),
        existing_nullable=False,
        postgresql_using="created_at AT TIME ZONE current_setting('TimeZone')",
    )
