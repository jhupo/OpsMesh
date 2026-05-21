"""create runtime leases

Revision ID: 0036_create_runtime_leases
Revises: 0035_skill_install_disabled_at
Create Date: 2026-05-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_create_runtime_leases"
down_revision: str | None = "0035_skill_install_disabled_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_leases",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_runtime_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_space_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("docker_container_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
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
        sa.ForeignKeyConstraint(["runtime_space_id"], ["runtime_spaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_runtime_id"],
            ["workspace_runtimes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_leases")),
        sa.UniqueConstraint("workspace_runtime_id", name="uq_runtime_leases_runtime"),
    )
    op.create_index("ix_runtime_leases_container", "runtime_leases", ["docker_container_id"])
    op.create_index(
        "ix_runtime_leases_runtime_space",
        "runtime_leases",
        ["workspace_id", "runtime_space_id"],
    )
    op.create_index(
        "ix_runtime_leases_workspace_status",
        "runtime_leases",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_leases_workspace_status", table_name="runtime_leases")
    op.drop_index("ix_runtime_leases_runtime_space", table_name="runtime_leases")
    op.drop_index("ix_runtime_leases_container", table_name="runtime_leases")
    op.drop_table("runtime_leases")
