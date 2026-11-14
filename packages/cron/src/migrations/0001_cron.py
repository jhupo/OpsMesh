"""Create plugin-owned schedule definitions without serialized callables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_cron"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create schedule definitions and their namespace constraint."""
    op.create_table(
        "cron_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("owner_plugin", sa.String(200), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("revision", sa.String(36), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("expired", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("namespace", "owner_plugin", "name"),
    )
    op.create_index(
        "ix_cron_schedules_namespace", "cron_schedules", ["namespace"]
    )


def downgrade() -> None:
    """Remove only the cron plugin's table."""
    op.drop_table("cron_schedules")
