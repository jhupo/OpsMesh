"""create workspace invites

Revision ID: 0047_workspace_invites
Revises: 0046_user_api_tokens
Create Date: 2026-06-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_workspace_invites"
down_revision: str | None = "0046_user_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_invites",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=80), nullable=False),
        sa.Column("inviter_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invitee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inviter_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invitee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["accepted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_invites")),
        sa.UniqueConstraint("token_hash", name="uq_workspace_invites_token_hash"),
    )
    op.create_index(
        "uq_workspace_invites_active_workspace_email",
        "workspace_invites",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_workspace_invites_email_status",
        "workspace_invites",
        ["email", "status"],
    )
    op.create_index(
        "ix_workspace_invites_fingerprint",
        "workspace_invites",
        ["fingerprint"],
    )
    op.create_index(
        "ix_workspace_invites_invitee_user_id",
        "workspace_invites",
        ["invitee_user_id"],
    )
    op.create_index(
        op.f("ix_workspace_invites_status"),
        "workspace_invites",
        ["status"],
    )
    op.create_index(
        "ix_workspace_invites_workspace_status",
        "workspace_invites",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_invites_workspace_status", table_name="workspace_invites")
    op.drop_index(op.f("ix_workspace_invites_status"), table_name="workspace_invites")
    op.drop_index("ix_workspace_invites_invitee_user_id", table_name="workspace_invites")
    op.drop_index("ix_workspace_invites_fingerprint", table_name="workspace_invites")
    op.drop_index("ix_workspace_invites_email_status", table_name="workspace_invites")
    op.drop_index("uq_workspace_invites_active_workspace_email", table_name="workspace_invites")
    op.drop_table("workspace_invites")
