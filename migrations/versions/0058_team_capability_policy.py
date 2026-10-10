"""add versioned team capability policy

Revision ID: 0058_team_capability_policy
Revises: 0057_dynamic_capabilities
Create Date: 2026-09-07 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_team_capability_policy"
down_revision: str | None = "0057_dynamic_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_teams",
        sa.Column(
            "capability_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_teams",
        sa.Column(
            "capability_policy_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_teams", "capability_policy_version")
    op.drop_column("agent_teams", "capability_policy")
