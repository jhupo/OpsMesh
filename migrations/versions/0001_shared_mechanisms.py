"""shared mechanisms: unique temporal outbox and creation receipts

Revision ID: 0001
Revises:
Create Date: 2026-10-16 00:00:00.000000+00:00

First migration of the V4 architecture. It creates only the tables owned by
the S2 shared mechanisms; feature tables arrive with their own steps and the
empty-database chain is sealed as the first release at S14. No historical
architecture migration is inherited.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "temporal_dispatches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("sealed_payload", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=100), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "operation IN ('start', 'signal', 'cancel')",
            name=op.f("ck_temporal_dispatches_operation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_temporal_dispatches")),
    )
    op.create_index(
        op.f("ix_temporal_dispatches_workflow_id"),
        "temporal_dispatches",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_temporal_dispatches_claim_token"),
        "temporal_dispatches",
        ["claim_token"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_dispatches_pending",
        "temporal_dispatches",
        ["published_at", "available_at"],
        unique=False,
    )
    op.create_table(
        "http_creation_receipts",
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=256), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "scope_id",
            "actor_id",
            "operation",
            "key_digest",
            name=op.f("pk_http_creation_receipts"),
        ),
    )


def downgrade() -> None:
    op.drop_table("http_creation_receipts")
    op.drop_index("ix_temporal_dispatches_pending", table_name="temporal_dispatches")
    op.drop_index(
        op.f("ix_temporal_dispatches_claim_token"), table_name="temporal_dispatches"
    )
    op.drop_index(
        op.f("ix_temporal_dispatches_workflow_id"), table_name="temporal_dispatches"
    )
    op.drop_table("temporal_dispatches")
