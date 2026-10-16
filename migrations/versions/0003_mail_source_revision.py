"""invitation mail token revision

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16 12:00:00.000000+00:00

One mail intent carries the revision of its source it was rendered for: an
invitation resend rotates the token, and the previous intent must be refused
at delivery instead of sending a link that no longer works. The column is
nullable because not every source is versioned; a versioned source's
admission contract requires it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mail_intents",
        sa.Column("source_revision", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mail_intents", "source_revision")
