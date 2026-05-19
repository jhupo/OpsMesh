"""create worker nodes and leases

Revision ID: 0026_worker_nodes_leases
Revises: 0025_runtime_spaces
Create Date: 2026-05-19 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_worker_nodes_leases"
down_revision: str | None = "0025_runtime_spaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_nodes",
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("worker_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("queue_name", sa.String(length=120), nullable=False),
        sa.Column("worker_version", sa.String(length=120), nullable=True),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("capacity", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("drain_requested_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_nodes")),
        sa.UniqueConstraint("worker_id", name="uq_worker_nodes_worker_id"),
    )
    op.create_index("ix_worker_nodes_status", "worker_nodes", ["status"])
    op.create_index("ix_worker_nodes_type_status", "worker_nodes", ["worker_type", "status"])

    op.create_table(
        "worker_leases",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("queue_name", sa.String(length=120), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_leases")),
        sa.UniqueConstraint("job_id", name="uq_worker_leases_job_id"),
    )
    op.create_index(
        "ix_worker_leases_resource",
        "worker_leases",
        ["workspace_id", "job_type", "resource_id"],
    )
    op.create_index(
        "ix_worker_leases_worker_status",
        "worker_leases",
        ["worker_id", "status"],
    )
    op.create_index(
        "ix_worker_leases_workspace_status",
        "worker_leases",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_leases_workspace_status", table_name="worker_leases")
    op.drop_index("ix_worker_leases_worker_status", table_name="worker_leases")
    op.drop_index("ix_worker_leases_resource", table_name="worker_leases")
    op.drop_table("worker_leases")
    op.drop_index("ix_worker_nodes_type_status", table_name="worker_nodes")
    op.drop_index("ix_worker_nodes_status", table_name="worker_nodes")
    op.drop_table("worker_nodes")
