"""create platform policies

Revision ID: 0027_platform_policies
Revises: 0026_worker_nodes_leases
Create Date: 2026-05-20 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_platform_policies"
down_revision: str | None = "0026_worker_nodes_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_policies",
        sa.Column("policy_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("updated_by", sa.String(length=160), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_policies")),
        sa.UniqueConstraint("policy_key", name="uq_platform_policies_key"),
    )
    op.create_index("ix_platform_policies_status", "platform_policies", ["status"])

    op.create_table(
        "platform_policy_events",
        sa.Column("platform_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("message", sa.String(length=512), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["platform_policy_id"],
            ["platform_policies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_policy_events")),
    )
    op.create_index(
        "ix_platform_policy_events_created",
        "platform_policy_events",
        ["created_at"],
    )
    op.create_index(
        "ix_platform_policy_events_policy",
        "platform_policy_events",
        ["platform_policy_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_policy_events_policy", table_name="platform_policy_events")
    op.drop_index("ix_platform_policy_events_created", table_name="platform_policy_events")
    op.drop_table("platform_policy_events")
    op.drop_index("ix_platform_policies_status", table_name="platform_policies")
    op.drop_table("platform_policies")
