"""Normalize the managed Docker runtime provider identifier."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088_normalize_managed_runtime_provider"
down_revision: str | None = "0087_worker_lease_claim_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE workspace_runtimes "
            "SET runtime_provider = 'cloud_docker' "
            "WHERE runtime_provider = 'docker'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE workspace_runtimes "
            "SET runtime_provider = 'docker' "
            "WHERE runtime_provider = 'cloud_docker'"
        )
    )
