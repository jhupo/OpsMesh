"""close correlation, audit, and model-attempt evidence

Revision ID: 0092_observability_evidence_closure
Revises: 0091_workspace_backup_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0092_observability_evidence_closure"
down_revision: str | None = "0091_workspace_backup_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_correlation_columns("audit_events", include_worker_runtime=True)
    op.create_index(
        "ix_audit_events_workspace_trace",
        "audit_events",
        ["workspace_id", "trace_id"],
    )
    op.create_index(
        "ix_audit_events_workspace_request",
        "audit_events",
        ["workspace_id", "request_id"],
    )

    _add_correlation_columns("run_events", include_worker_runtime=True)
    op.create_index(
        "ix_run_events_workspace_trace",
        "run_events",
        ["workspace_id", "trace_id"],
    )
    op.create_index(
        "ix_run_events_workspace_request",
        "run_events",
        ["workspace_id", "request_id"],
    )

    _add_correlation_columns("worker_leases", include_worker_runtime=False)
    op.add_column(
        "worker_leases",
        sa.Column("runtime_id", sa.String(length=120), nullable=True),
    )
    op.create_index(
        "ix_worker_leases_workspace_trace",
        "worker_leases",
        ["workspace_id", "trace_id"],
    )
    op.create_index(
        "ix_worker_leases_workspace_request",
        "worker_leases",
        ["workspace_id", "request_id"],
    )
    op.create_index(
        "ix_worker_leases_workspace_runtime",
        "worker_leases",
        ["workspace_id", "runtime_id"],
    )

    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("request_id", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("runtime_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "mcp_tool_call_logs",
        sa.Column("worker_id", sa.String(length=160), nullable=True),
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_request",
        "mcp_tool_call_logs",
        ["workspace_id", "request_id"],
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_worker",
        "mcp_tool_call_logs",
        ["workspace_id", "worker_id"],
    )
    op.create_index(
        "ix_mcp_tool_call_logs_workspace_runtime",
        "mcp_tool_call_logs",
        ["workspace_id", "runtime_id"],
    )

    _add_correlation_columns("runtime_events", include_worker_runtime=True)
    op.create_index(
        "ix_runtime_events_workspace_trace",
        "runtime_events",
        ["workspace_id", "trace_id"],
    )
    op.create_index(
        "ix_runtime_events_workspace_request",
        "runtime_events",
        ["workspace_id", "request_id"],
    )

    op.drop_constraint(
        "uq_model_usage_records_run_attempt",
        "model_usage_records",
        type_="unique",
    )
    op.add_column(
        "model_usage_records",
        sa.Column("request_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "model_usage_records",
        sa.Column("attempt_outcome", sa.String(length=32), nullable=False, server_default="succeeded"),
    )
    op.add_column(
        "model_usage_records",
        sa.Column("error_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "model_usage_records",
        sa.Column(
            "budget_decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "model_usage_records",
        sa.Column("request_id", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "model_usage_records",
        sa.Column("span_id", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "model_usage_records",
        sa.Column("worker_id", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "model_usage_records",
        sa.Column("runtime_id", sa.String(length=120), nullable=True),
    )
    op.alter_column("model_usage_records", "request_sequence", server_default=None)
    op.alter_column("model_usage_records", "attempt_outcome", server_default=None)
    op.alter_column("model_usage_records", "budget_decision", server_default=None)
    op.create_check_constraint(
        "ck_model_usage_request_sequence",
        "model_usage_records",
        "request_sequence >= 0",
    )
    op.create_check_constraint(
        "ck_model_usage_attempt_outcome",
        "model_usage_records",
        "attempt_outcome IN ('succeeded', 'failed', 'cancelled')",
    )
    op.create_unique_constraint(
        "uq_model_usage_records_run_job_attempt_request",
        "model_usage_records",
        ["workspace_id", "agent_run_id", "job_attempt", "request_sequence"],
    )
    op.create_index(
        "ix_model_usage_workspace_trace",
        "model_usage_records",
        ["workspace_id", "trace_id"],
    )
    op.create_index(
        "ix_model_usage_workspace_request",
        "model_usage_records",
        ["workspace_id", "request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_usage_workspace_request", table_name="model_usage_records")
    op.drop_index("ix_model_usage_workspace_trace", table_name="model_usage_records")
    op.drop_constraint(
        "uq_model_usage_records_run_job_attempt_request",
        "model_usage_records",
        type_="unique",
    )
    # The previous schema can retain only one provider request per worker attempt.
    # Keep the last request deterministically when downgrading a fallback run.
    op.execute(
        sa.text(
            """
            DELETE FROM model_usage_records
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT
                        id,
                        row_number() OVER (
                            PARTITION BY workspace_id, agent_run_id, job_attempt
                            ORDER BY request_sequence DESC, occurred_at DESC, id DESC
                        ) AS row_number
                    FROM model_usage_records
                ) ranked
                WHERE row_number > 1
            )
            """
        )
    )
    op.create_unique_constraint(
        "uq_model_usage_records_run_attempt",
        "model_usage_records",
        ["workspace_id", "agent_run_id", "job_attempt"],
    )
    op.drop_constraint(
        "ck_model_usage_attempt_outcome",
        "model_usage_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_model_usage_request_sequence",
        "model_usage_records",
        type_="check",
    )
    op.drop_column("model_usage_records", "runtime_id")
    op.drop_column("model_usage_records", "worker_id")
    op.drop_column("model_usage_records", "span_id")
    op.drop_column("model_usage_records", "request_id")
    op.drop_column("model_usage_records", "budget_decision")
    op.drop_column("model_usage_records", "error_code")
    op.drop_column("model_usage_records", "attempt_outcome")
    op.drop_column("model_usage_records", "request_sequence")

    op.drop_index(
        "ix_mcp_tool_call_logs_workspace_request",
        table_name="mcp_tool_call_logs",
    )
    op.drop_index(
        "ix_mcp_tool_call_logs_workspace_worker",
        table_name="mcp_tool_call_logs",
    )
    op.drop_index(
        "ix_mcp_tool_call_logs_workspace_runtime",
        table_name="mcp_tool_call_logs",
    )
    op.drop_column("mcp_tool_call_logs", "worker_id")
    op.drop_column("mcp_tool_call_logs", "runtime_id")
    op.drop_column("mcp_tool_call_logs", "request_id")

    op.drop_index("ix_runtime_events_workspace_request", table_name="runtime_events")
    op.drop_index("ix_runtime_events_workspace_trace", table_name="runtime_events")
    _drop_correlation_columns("runtime_events", include_worker_runtime=True)

    op.drop_index("ix_worker_leases_workspace_request", table_name="worker_leases")
    op.drop_index("ix_worker_leases_workspace_trace", table_name="worker_leases")
    op.drop_index("ix_worker_leases_workspace_runtime", table_name="worker_leases")
    op.drop_column("worker_leases", "runtime_id")
    _drop_correlation_columns("worker_leases", include_worker_runtime=False)

    op.drop_index("ix_run_events_workspace_request", table_name="run_events")
    op.drop_index("ix_run_events_workspace_trace", table_name="run_events")
    _drop_correlation_columns("run_events", include_worker_runtime=True)

    op.drop_index("ix_audit_events_workspace_request", table_name="audit_events")
    op.drop_index("ix_audit_events_workspace_trace", table_name="audit_events")
    _drop_correlation_columns("audit_events", include_worker_runtime=True)


def _add_correlation_columns(table_name: str, *, include_worker_runtime: bool) -> None:
    op.add_column(table_name, sa.Column("request_id", sa.String(length=80), nullable=True))
    op.add_column(table_name, sa.Column("trace_id", sa.String(length=32), nullable=True))
    op.add_column(table_name, sa.Column("span_id", sa.String(length=16), nullable=True))
    if include_worker_runtime:
        op.add_column(table_name, sa.Column("worker_id", sa.String(length=160), nullable=True))
        op.add_column(table_name, sa.Column("runtime_id", sa.String(length=120), nullable=True))


def _drop_correlation_columns(table_name: str, *, include_worker_runtime: bool) -> None:
    if include_worker_runtime:
        op.drop_column(table_name, "runtime_id")
        op.drop_column(table_name, "worker_id")
    op.drop_column(table_name, "span_id")
    op.drop_column(table_name, "trace_id")
    op.drop_column(table_name, "request_id")
