"""add worker lease heartbeat freshness

Revision ID: 0037_worker_lease_heartbeat
Revises: 0036_create_runtime_leases
Create Date: 2026-06-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_worker_lease_heartbeat"
down_revision: str | None = "0036_create_runtime_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "worker_leases",
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
    )
    op.execute("UPDATE worker_leases SET last_heartbeat_at = started_at WHERE status = 'running'")
    op.create_index(
        "ix_worker_leases_status_heartbeat",
        "worker_leases",
        ["status", "last_heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_leases_status_heartbeat", table_name="worker_leases")
    op.drop_column("worker_leases", "last_heartbeat_at")
