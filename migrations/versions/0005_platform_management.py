"""Versioned configuration, automation, evaluations and monetary reservations."""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_platform_management"
down_revision = "0004_native_gateway"
branch_labels = None
depends_on = None


def col(name: str, type_: Any, reference: str | None = None, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(
        name,
        type_,
        *([sa.ForeignKey(reference)] if reference else []),
        nullable=kwargs.pop("nullable", False),
        **kwargs,
    )


def timestamps() -> list[sa.Column[Any]]:
    return [
        col(name, sa.DateTime(timezone=True), server_default=sa.func.now())
        for name in ("created_at", "updated_at")
    ]


def uuid_id() -> sa.Column[Any]:
    return col("id", sa.Uuid(), primary_key=True)


def owner() -> sa.Column[Any]:
    return col("owner_user_id", sa.Uuid(), "users.id")


def upgrade() -> None:
    op.create_table(
        "agent_catalog_entries",
        *timestamps(),
        col("kind", sa.String(16), primary_key=True),
        col("id", sa.String(200), primary_key=True),
        col("owner_user_id", sa.Uuid(), "users.id", nullable=True),
        col("revision", sa.Integer()),
        col("published_number", sa.Integer()),
        col("visibility", sa.String(16)),
        col("encrypted_draft", sa.Text()),
        col("key_id", sa.String(100)),
        sa.CheckConstraint("kind IN ('agent', 'provider')", name="catalog_kind"),
        sa.CheckConstraint("revision > 0 AND published_number >= 0", name="catalog_revision"),
        sa.CheckConstraint("visibility IN ('private', 'public')", name="catalog_visibility"),
    )
    op.create_index(
        "ix_agent_catalog_entries_owner_user_id", "agent_catalog_entries", ["owner_user_id"]
    )
    op.create_table(
        "agent_catalog_versions",
        *timestamps(),
        col("kind", sa.String(16), primary_key=True),
        col("entry_id", sa.String(200), primary_key=True),
        col("number", sa.Integer(), primary_key=True),
        col("encrypted_payload", sa.Text()),
        col("key_id", sa.String(100)),
        col("digest", sa.String(64)),
        sa.CheckConstraint("kind IN ('agent', 'provider')", name="catalog_version_kind"),
        sa.CheckConstraint("number > 0", name="catalog_version_number"),
        sa.ForeignKeyConstraint(
            ["kind", "entry_id"], ["agent_catalog_entries.kind", "agent_catalog_entries.id"]
        ),
    )
    op.create_table(
        "agent_catalog_credentials",
        *timestamps(),
        col("id", sa.String(200), primary_key=True),
        col("revision", sa.Integer()),
        col("encrypted_secret", sa.Text()),
        col("key_id", sa.String(100)),
    )
    op.create_table(
        "spending_limits",
        *timestamps(),
        col("user_id", sa.Uuid(), "users.id", primary_key=True),
        col("currency", sa.String(3), primary_key=True),
        col("amount", sa.Numeric(24, 8), nullable=True),
        col("warning_ratio", sa.Numeric(5, 4)),
        col("revision", sa.Integer()),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="spending_limit_amount"),
    )
    op.create_table(
        "spending_accounts",
        *timestamps(),
        col("user_id", sa.Uuid(), "users.id", primary_key=True),
        col("currency", sa.String(3), primary_key=True),
        col("period", sa.String(7), primary_key=True),
        col("spent", sa.Numeric(24, 8)),
        col("reserved", sa.Numeric(24, 8)),
        sa.CheckConstraint("spent >= 0 AND reserved >= 0", name="spending_account_amounts"),
    )
    op.create_table(
        "model_charges",
        *timestamps(),
        col("operation_id", sa.String(500), primary_key=True),
        col("run_id", sa.Uuid(), "execution_runs.id"),
        col("user_id", sa.Uuid(), "users.id"),
        col("provider_id", sa.String(200), nullable=True),
        col("model_id", sa.String(200), nullable=True),
        col("currency", sa.String(3), nullable=True),
        col("period", sa.String(7)),
        col("pricing", JSONB(), nullable=True),
        col("reserved", sa.Numeric(24, 8)),
        col("amount", sa.Numeric(24, 8), nullable=True),
        col("input_tokens", sa.Integer(), nullable=True),
        col("output_tokens", sa.Integer(), nullable=True),
        col("status", sa.String(16)),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released')", name="model_charge_status"
        ),
        sa.CheckConstraint(
            "reserved >= 0 AND (amount IS NULL OR amount >= 0)", name="model_charge_amounts"
        ),
    )
    for name in ("run_id", "user_id"):
        op.create_index(f"ix_model_charges_{name}", "model_charges", [name])
    op.create_table(
        "workflow_triggers",
        uuid_id(),
        *timestamps(),
        owner(),
        col("kind", sa.String(16)),
        col("revision", sa.Integer()),
        col("applied_revision", sa.Integer()),
        col("encrypted_spec", sa.Text()),
        col("encrypted_secret", sa.Text(), nullable=True),
        col("key_id", sa.String(100)),
        col("status", sa.String(16)),
        col("sync_error", sa.String(100), nullable=True),
        sa.CheckConstraint("kind IN ('schedule', 'webhook')", name="trigger_kind"),
        sa.CheckConstraint("revision > 0 AND applied_revision >= 0", name="trigger_revision"),
    )
    op.create_index("ix_workflow_triggers_owner_user_id", "workflow_triggers", ["owner_user_id"])
    op.create_table(
        "workflow_trigger_deliveries",
        uuid_id(),
        *timestamps(),
        col("trigger_id", sa.Uuid(), "workflow_triggers.id"),
        col("event_id", sa.String(200)),
        col("revision", sa.Integer()),
        col("fingerprint", sa.String(64)),
        col("target_kind", sa.String(16)),
        col("run_id", sa.Uuid()),
        sa.UniqueConstraint("trigger_id", "event_id"),
    )
    op.create_index(
        "ix_workflow_trigger_deliveries_trigger_id", "workflow_trigger_deliveries", ["trigger_id"]
    )
    op.create_table(
        "evaluation_datasets",
        uuid_id(),
        *timestamps(),
        owner(),
        col("name", sa.String(120)),
        col("version", sa.Integer()),
    )
    op.create_index(
        "ix_evaluation_datasets_owner_user_id", "evaluation_datasets", ["owner_user_id"]
    )
    op.create_table(
        "evaluation_dataset_versions",
        *timestamps(),
        col("dataset_id", sa.Uuid(), "evaluation_datasets.id", primary_key=True),
        col("number", sa.Integer(), primary_key=True),
        col("digest", sa.String(64)),
        col("encrypted_spec", sa.Text()),
        col("key_id", sa.String(100)),
    )
    op.create_table(
        "evaluation_experiments",
        uuid_id(),
        *timestamps(),
        owner(),
        col("dataset_id", sa.Uuid(), "evaluation_datasets.id"),
        col("dataset_version", sa.Integer()),
        col("request_key", sa.String(200)),
        col("fingerprint", sa.String(64)),
        col("status", sa.String(24)),
        col("encrypted_spec", sa.Text()),
        col("key_id", sa.String(100)),
        sa.UniqueConstraint("owner_user_id", "request_key"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'needs_attention', 'cancelling', 'completed', 'cancelled', 'failed')",
            name="evaluation_experiment_status",
        ),
    )
    for name in ("owner_user_id", "dataset_id"):
        op.create_index(f"ix_evaluation_experiments_{name}", "evaluation_experiments", [name])
    op.create_table(
        "evaluation_cases",
        uuid_id(),
        *timestamps(),
        col("experiment_id", sa.Uuid(), "evaluation_experiments.id"),
        col("case_id", sa.String(120)),
        col("target_id", sa.Uuid()),
        col("target_kind", sa.String(16)),
        col("status", sa.String(24)),
        col("run_id", sa.Uuid(), nullable=True),
        col("encrypted_result", sa.Text(), nullable=True),
        col("key_id", sa.String(100), nullable=True),
        col("error_code", sa.String(100), nullable=True),
        sa.UniqueConstraint("experiment_id", "case_id", "target_id"),
    )
    op.create_index("ix_evaluation_cases_experiment_id", "evaluation_cases", ["experiment_id"])
    op.create_table(
        "run_feedback",
        uuid_id(),
        *timestamps(),
        owner(),
        col("target_kind", sa.String(16)),
        col("run_id", sa.Uuid()),
        col("rating", sa.Integer()),
        col("encrypted_payload", sa.Text()),
        col("key_id", sa.String(100)),
        sa.UniqueConstraint("owner_user_id", "target_kind", "run_id"),
    )
    op.create_index("ix_run_feedback_owner_user_id", "run_feedback", ["owner_user_id"])


def downgrade() -> None:
    for name in (
        "run_feedback",
        "evaluation_cases",
        "evaluation_experiments",
        "evaluation_dataset_versions",
        "evaluation_datasets",
        "workflow_trigger_deliveries",
        "workflow_triggers",
        "model_charges",
        "spending_accounts",
        "spending_limits",
        "agent_catalog_credentials",
        "agent_catalog_versions",
        "agent_catalog_entries",
    ):
        op.drop_table(name)
