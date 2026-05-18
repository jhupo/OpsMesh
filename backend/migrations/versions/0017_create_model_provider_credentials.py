"""create model provider credentials

Revision ID: 0017_model_provider_credentials
Revises: 0016_workspace_export_jobs
Create Date: 2026-05-18 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_model_provider_credentials"
down_revision: str | None = "0016_workspace_export_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_provider_credentials",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("default_model", sa.String(length=120), nullable=False),
        sa.Column("encrypted_api_key", sa.String(), nullable=False),
        sa.Column("api_key_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=120), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_provider_credentials")),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_model_provider_credentials_name",
        ),
    )
    op.create_index(
        "ix_model_provider_credentials_workspace_status",
        "model_provider_credentials",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_model_provider_credentials_workspace_default",
        "model_provider_credentials",
        ["workspace_id", "is_default"],
    )
    op.add_column(
        "agent_profiles",
        sa.Column(
            "model_provider_credential_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        op.f("fk_agent_profiles_model_provider_credential_id_model_provider_credentials"),
        "agent_profiles",
        "model_provider_credentials",
        ["model_provider_credential_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_agent_profiles_model_provider_credential_id_model_provider_credentials"),
        "agent_profiles",
        type_="foreignkey",
    )
    op.drop_column("agent_profiles", "model_provider_credential_id")
    op.drop_index(
        "ix_model_provider_credentials_workspace_default",
        table_name="model_provider_credentials",
    )
    op.drop_index(
        "ix_model_provider_credentials_workspace_status",
        table_name="model_provider_credentials",
    )
    op.drop_table("model_provider_credentials")
