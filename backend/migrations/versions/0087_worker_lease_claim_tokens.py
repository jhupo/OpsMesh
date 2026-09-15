"""Add ownership tokens for worker lease updates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_worker_lease_claim_tokens"
down_revision: str | None = "0086_knowledge_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "worker_leases",
        sa.Column("claim_token", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("worker_leases", "claim_token")
