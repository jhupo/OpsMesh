"""add user API token scopes

Revision ID: 0056_user_token_scopes
Revises: 0055_observability_cost_audit
Create Date: 2026-09-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056_user_token_scopes"
down_revision: str | None = "0055_observability_cost_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_api_tokens",
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("user_api_tokens", "scopes")
