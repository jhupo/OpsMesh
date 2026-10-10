"""add database check constraints

Revision ID: 0039_db_check_constraints
Revises: 0038_provider_default_memory_fts
Create Date: 2026-06-04 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_db_check_constraints"
down_revision: str | None = "0038_provider_default_memory_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("worker_leases") as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_worker_leases_status_valid"),
            "status IN ('running', 'completed', 'failed', 'retrying', 'expired')",
        )
        batch_op.create_check_constraint(
            op.f("ck_worker_leases_attempt_non_negative"),
            "attempt >= 0",
        )

    for table_name in ("workspace_quotas", "runtime_space_quotas"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.create_check_constraint(
                op.f(f"ck_{table_name}_limit_value_non_negative"),
                "limit_value >= 0",
            )
            batch_op.create_check_constraint(
                op.f(f"ck_{table_name}_reserved_value_non_negative"),
                "reserved_value >= 0",
            )


def downgrade() -> None:
    for table_name in ("runtime_space_quotas", "workspace_quotas"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(
                op.f(f"ck_{table_name}_reserved_value_non_negative"),
                type_="check",
            )
            batch_op.drop_constraint(
                op.f(f"ck_{table_name}_limit_value_non_negative"),
                type_="check",
            )

    with op.batch_alter_table("worker_leases") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_worker_leases_attempt_non_negative"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_worker_leases_status_valid"),
            type_="check",
        )
