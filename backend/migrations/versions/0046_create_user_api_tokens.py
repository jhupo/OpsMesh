"""create user api tokens

Revision ID: 0046_user_api_tokens
Revises: 0045_scheduled_jobs
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_user_api_tokens"
down_revision: str | None = "0045_scheduled_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_api_tokens",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_api_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_user_api_tokens_hash"),
    )
    op.create_index(
        "ix_user_api_tokens_fingerprint",
        "user_api_tokens",
        ["fingerprint"],
    )
    op.create_index(
        "ix_user_api_tokens_user_status",
        "user_api_tokens",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_api_tokens_user_status", table_name="user_api_tokens")
    op.drop_index("ix_user_api_tokens_fingerprint", table_name="user_api_tokens")
    op.drop_table("user_api_tokens")
