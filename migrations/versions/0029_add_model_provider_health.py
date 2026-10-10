"""add model provider health

Revision ID: 0029_add_model_provider_health
Revises: 0028_create_workspace_quotas
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_add_model_provider_health"
down_revision: str | None = "0028_create_workspace_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_provider_credentials",
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "model_provider_credentials",
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "model_provider_credentials",
        sa.Column("last_failure_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "model_provider_credentials",
        sa.Column("last_failure_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "model_provider_credentials",
        sa.Column("last_failure_message", sa.String(length=1000), nullable=True),
    )
    op.alter_column("model_provider_credentials", "health_status", server_default=None)


def downgrade() -> None:
    op.drop_column("model_provider_credentials", "last_failure_message")
    op.drop_column("model_provider_credentials", "last_failure_code")
    op.drop_column("model_provider_credentials", "last_failure_at")
    op.drop_column("model_provider_credentials", "last_success_at")
    op.drop_column("model_provider_credentials", "health_status")
