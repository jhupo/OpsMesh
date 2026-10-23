"""Persist workflow instances, runs, nodes, signals and effect receipts.

Existing definition and legacy execution history is retained without conversion.
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0003_workflow_instances"
down_revision = "0002_execution_instances"
branch_labels = None
depends_on = None


def uuid(name: str, reference: str | None = None, **options: Any) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(),
        *([sa.ForeignKey(reference)] if reference else []),
        nullable=options.pop("nullable", False),
        **options,
    )


def string(name: str, length: int, **options: Any) -> sa.Column[Any]:
    return sa.Column(name, sa.String(length), nullable=options.pop("nullable", False), **options)


def text(name: str, **options: Any) -> sa.Column[Any]:
    return sa.Column(name, sa.Text(), nullable=options.pop("nullable", False), **options)


def timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_table(
        "workflow_instances",
        uuid("id", primary_key=True),
        uuid("owner_user_id", "users.id"),
        uuid("revision_id", "workflow_revisions.id"),
        string("name", 200),
        string("status", 24),
        string("request_key", 200),
        string("fingerprint", 64),
        text("encrypted_prepared"),
        string("key_id", 100),
        sa.UniqueConstraint("owner_user_id", "request_key"),
        *timestamps(),
    )
    op.create_index("ix_workflow_instances_owner_user_id", "workflow_instances", ["owner_user_id"])
    op.create_index("ix_workflow_instances_revision_id", "workflow_instances", ["revision_id"])
    op.create_table(
        "workflow_runs",
        uuid("id", primary_key=True),
        uuid("user_id", "users.id"),
        uuid("instance_id", "workflow_instances.id"),
        string("engine", 24),
        string("request_key", 200),
        string("fingerprint", 64),
        string("status", 32),
        text("encrypted_input"),
        text("encrypted_result", nullable=True),
        string("key_id", 100),
        string("error_code", 100, nullable=True),
        sa.UniqueConstraint("user_id", "request_key"),
        *timestamps(),
    )
    op.create_index("ix_workflow_runs_user_id", "workflow_runs", ["user_id"])
    op.create_index("ix_workflow_runs_instance_id", "workflow_runs", ["instance_id"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_table(
        "workflow_node_runs",
        uuid("id", primary_key=True),
        uuid("run_id", "workflow_runs.id"),
        string("path", 2000),
        string("kind", 32),
        string("fingerprint", 64),
        string("status", 32),
        uuid("execution_run_id", "execution_runs.id", nullable=True),
        text("encrypted_input"),
        text("encrypted_result", nullable=True),
        text("encrypted_descriptor", nullable=True),
        uuid("approval_id", nullable=True),
        sa.Column("approval_decision", sa.Boolean(), nullable=True),
        string("error_code", 100, nullable=True),
        sa.UniqueConstraint("run_id", "path"),
        *timestamps(),
    )
    op.create_index("ix_workflow_node_runs_run_id", "workflow_node_runs", ["run_id"])
    op.create_index(
        "ix_workflow_node_runs_execution_run_id", "workflow_node_runs", ["execution_run_id"]
    )
    op.create_table(
        "workflow_operations",
        string("id", 200, primary_key=True),
        uuid("run_id", "workflow_runs.id"),
        uuid("node_id", "workflow_node_runs.id"),
        string("fingerprint", 64),
        string("status", 32),
        text("encrypted_result", nullable=True),
        text("encrypted_reconciliation", nullable=True),
        *timestamps(),
    )
    op.create_index("ix_workflow_operations_run_id", "workflow_operations", ["run_id"])
    op.create_table(
        "workflow_events",
        uuid("run_id", "workflow_runs.id", primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        string("kind", 64),
        text("encrypted_payload"),
        string("key_id", 100),
        *timestamps(),
    )

    op.create_table(
        "workflow_signals",
        uuid("id", primary_key=True),
        uuid("run_id", "workflow_runs.id"),
        string("path", 2000),
        string("name", 64),
        string("request_key", 200),
        string("fingerprint", 64),
        text("encrypted_payload"),
        string("status", 24),
        sa.UniqueConstraint("run_id", "path", "name"),
        *timestamps(),
    )
    op.create_index("ix_workflow_signals_run_id", "workflow_signals", ["run_id"])


def downgrade() -> None:
    op.drop_table("workflow_signals")
    op.drop_table("workflow_events")
    op.drop_table("workflow_operations")
    op.drop_table("workflow_node_runs")
    op.drop_table("workflow_runs")
    op.drop_table("workflow_instances")
