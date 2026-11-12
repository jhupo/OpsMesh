"""Create execution call and usage observation tables.

Revision ID: 0001_execution_observations
Revises:
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_execution_observations"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the execution observation schema."""
    op.create_table(
        "execution_calls",
        sa.Column("call_id", sa.String(length=64), primary_key=True),
        sa.Column("parent_call_id", sa.String(length=64)),
        sa.Column("usage_owner_call_id", sa.String(length=64)),
        sa.Column("usage_relation", sa.String(length=20), nullable=False),
        sa.Column("host_instance", sa.String(length=200), nullable=False),
        sa.Column("plugin_key", sa.String(length=200)),
        sa.Column("agent_key", sa.String(length=200)),
        sa.Column("agent_name", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.String(length=200)),
        sa.Column("trace_id", sa.String(length=200)),
        sa.Column("session_group_id", sa.String(length=200)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("usage_state", sa.String(length=20), nullable=False),
        sa.Column("safe_error_code", sa.String(length=120)),
        sa.Column("cleanup_state", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('running', 'completed', 'interrupted', "
            "'failed', 'cancelled', 'abandoned')",
            name="ck_execution_calls_outcome",
        ),
        sa.CheckConstraint(
            "usage_state IN ('complete', 'partial', 'unknown')",
            name="ck_execution_calls_usage_state",
        ),
        sa.CheckConstraint(
            "usage_relation IN ('independent', 'included', 'unknown')",
            name="ck_execution_calls_usage_relation",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('not_required', 'completed', 'failed')",
            name="ck_execution_calls_cleanup_state",
        ),
    )
    op.create_index(
        "ix_execution_calls_scope",
        "execution_calls",
        ["plugin_key", "agent_key", "owner_id", "started_at"],
    )
    op.create_index(
        "ix_execution_calls_parent",
        "execution_calls",
        ["parent_call_id"],
    )
    op.create_index(
        "ix_execution_calls_host_outcome",
        "execution_calls",
        ["host_instance", "outcome"],
    )
    op.create_table(
        "execution_usage_observations",
        sa.Column("call_id", sa.String(length=64), primary_key=True),
        sa.Column("observation_key", sa.String(length=100), primary_key=True),
        sa.Column("request_count", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("native_details", sa.JSON(), nullable=False),
        sa.Column("coverage", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["execution_calls.call_id"],
            name=("fk_execution_usage_observations_call_id_execution_calls"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "request_count >= 0",
            name="ck_execution_usage_observations_request_count_nonnegative",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0",
            name="ck_execution_usage_observations_input_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "output_tokens >= 0",
            name=("ck_execution_usage_observations_output_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "total_tokens >= 0",
            name=("ck_execution_usage_observations_total_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "coverage IN ('complete', 'partial', 'unknown')",
            name="ck_execution_usage_observations_coverage",
        ),
    )
    op.create_index(
        "ix_execution_usage_observations_call_id",
        "execution_usage_observations",
        ["call_id"],
    )


def downgrade() -> None:
    """Drop the execution observation schema."""
    op.drop_index(
        "ix_execution_usage_observations_call_id",
        table_name="execution_usage_observations",
    )
    op.drop_table("execution_usage_observations")
    op.drop_index(
        "ix_execution_calls_host_outcome",
        table_name="execution_calls",
    )
    op.drop_index(
        "ix_execution_calls_parent",
        table_name="execution_calls",
    )
    op.drop_index(
        "ix_execution_calls_scope",
        table_name="execution_calls",
    )
    op.drop_table("execution_calls")
