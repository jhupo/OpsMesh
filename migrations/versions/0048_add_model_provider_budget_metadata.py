"""add model provider budget metadata

Revision ID: 0048_model_provider_budget
Revises: 0047_workspace_invites
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_model_provider_budget"
down_revision: str | None = "0047_workspace_invites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    json_type = postgresql.JSONB() if dialect_name == "postgresql" else sa.JSON()
    json_default = sa.text("'{}'::jsonb") if dialect_name == "postgresql" else sa.text("'{}'")

    op.add_column(
        "model_provider_credentials",
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "model_provider_credentials",
        sa.Column("budget_metadata", json_type, nullable=False, server_default=json_default),
    )
    with op.batch_alter_table("model_provider_credentials") as batch_op:
        batch_op.create_check_constraint(
            op.f("ck_model_provider_credentials_failure_count"),
            "failure_count >= 0",
        )
    op.alter_column("model_provider_credentials", "failure_count", server_default=None)
    op.alter_column("model_provider_credentials", "budget_metadata", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("model_provider_credentials") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_model_provider_credentials_failure_count"),
            type_="check",
        )
    op.drop_column("model_provider_credentials", "budget_metadata")
    op.drop_column("model_provider_credentials", "failure_count")
