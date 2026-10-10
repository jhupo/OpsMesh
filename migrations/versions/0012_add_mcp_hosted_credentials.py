"""add mcp hosted credentials

Revision ID: 0012_mcp_hosted_credentials
Revises: 0011_security_events
Create Date: 2026-05-17 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_mcp_hosted_credentials"
down_revision: str | None = "0011_security_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_credential_references",
        sa.Column("encrypted_secret_payload", sa.String(), nullable=True),
    )
    op.add_column(
        "mcp_credential_references",
        sa.Column("secret_fingerprint", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "mcp_credential_references",
        sa.Column("encryption_key_id", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_credential_references", "encryption_key_id")
    op.drop_column("mcp_credential_references", "secret_fingerprint")
    op.drop_column("mcp_credential_references", "encrypted_secret_payload")
