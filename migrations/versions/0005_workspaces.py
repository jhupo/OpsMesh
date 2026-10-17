"""workspaces: tenants, members, invitations, projects, credentials, capacity, model providers and notifications

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17 00:00:00.000000+00:00

S6 tables: the workspace tenant root with its membership, invitation,
project, credential, capacity, model provider connection and notification
facts. Every foreign key is explicit with ondelete RESTRICT except the
project membership and invitation rows, which cascade with the space
membership they are composed from. No historical architecture migration
is inherited.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
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
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')", name="workspace_status"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspaces_created_by_user_id_users"),
        ),
    )
    op.create_index(op.f("ix_workspaces_status"), "workspaces", ["status"], unique=False)
    op.create_table(
        "workspace_members",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id", "user_id", name=op.f("pk_workspace_members")
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name="workspace_member_role"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_members_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
        ),
    )
    op.create_index(
        "ix_workspace_members_user", "workspace_members", ["user_id"], unique=False
    )
    op.create_index(
        "uq_workspace_owner",
        "workspace_members",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.create_table(
        "workspace_invitations",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("invitee_id", sa.Uuid(), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_invitations")),
        sa.CheckConstraint(
            "role IN ('admin', 'member', 'viewer')", name="workspace_invitation_role"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'accepted', 'revoked', 'expired')",
            name="workspace_invitation_state",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_invitations_invited_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["invitee_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_invitations_invitee_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_invitations_workspace_id_workspaces"),
        ),
    )
    op.create_index(
        "ix_workspace_invitations_invitee",
        "workspace_invitations",
        ["invitee_id"],
        unique=False,
    )
    op.create_index(
        "uq_workspace_pending_invitation",
        "workspace_invitations",
        ["workspace_id", "invitee_id"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_table(
        "projects",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')", name="project_status"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_projects_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_projects_workspace_id_workspaces"),
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_project_workspace"),
    )
    op.create_index(op.f("ix_projects_workspace_id"), "projects", ["workspace_id"], unique=False)
    op.create_index(op.f("ix_projects_status"), "projects", ["status"], unique=False)
    op.create_table(
        "project_members",
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
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "user_id", name=op.f("pk_project_members")),
        sa.CheckConstraint(
            "role IN ('admin', 'member', 'viewer')", name="project_member_role"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_project_members_workspace_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
            name="fk_project_members_workspace_id_workspace_members",
        ),
    )
    op.create_index(
        "ix_project_members_workspace_user",
        "project_members",
        ["workspace_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "project_invitations",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("invitee_id", sa.Uuid(), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_invitations")),
        sa.CheckConstraint(
            "role IN ('admin', 'member', 'viewer')", name="project_invitation_role"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'accepted', 'revoked', 'expired')",
            name="project_invitation_state",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_project_invitations_invited_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "invitee_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
            name="fk_project_invitations_workspace_id_workspace_members",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_project_invitations_workspace_id_projects",
        ),
    )
    op.create_index(
        "ix_project_invitations_invitee",
        "project_invitations",
        ["invitee_id"],
        unique=False,
    )
    op.create_index(
        "uq_project_pending_invitation",
        "project_invitations",
        ["project_id", "invitee_id"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_table(
        "workspace_credentials",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=80), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_credentials")),
        sa.CheckConstraint(
            "kind IN ('model_provider', 'capability')", name="workspace_credential_kind"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="workspace_credential_status"
        ),
        sa.CheckConstraint("revision >= 1", name="workspace_credential_revision"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_credentials_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_credentials_workspace_id_workspaces"),
        ),
    )
    op.create_index(
        op.f("ix_workspace_credentials_workspace_id"),
        "workspace_credentials",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_credentials_status"),
        "workspace_credentials",
        ["status"],
        unique=False,
    )
    op.create_index(
        "uq_workspace_active_credential_name",
        "workspace_credentials",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "workspace_capacity_accounts",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("limit_value", sa.BigInteger(), nullable=True),
        sa.Column("reserved_value", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace_id", "kind", name=op.f("pk_workspace_capacity_accounts")
        ),
        sa.CheckConstraint(
            "kind IN ('concurrency', 'environment')", name="workspace_capacity_kind"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_capacity_accounts_workspace_id_workspaces"),
        ),
    )
    op.create_table(
        "workspace_capacity_reservations",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_scope", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_capacity_reservations")),
        sa.CheckConstraint("amount > 0", name="workspace_capacity_reservation_amount"),
        sa.CheckConstraint(
            "status IN ('reserved', 'released')",
            name="workspace_capacity_reservation_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_capacity_reservations_workspace_id_workspaces"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "kind",
            "idempotency_scope",
            "idempotency_key",
            name="uq_workspace_capacity_reservations_scope_key",
        ),
    )
    op.create_index(
        op.f("ix_workspace_capacity_reservations_workspace_id"),
        "workspace_capacity_reservations",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "model_provider_connections",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("default_model", sa.String(length=200), nullable=False),
        sa.Column("models", postgresql.JSONB(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_probe_status", sa.String(length=32), nullable=False),
        sa.Column("last_probe_detail", sa.String(length=500), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_provider_connections")),
        sa.CheckConstraint(
            "provider = 'openai'", name="model_provider_connection_provider"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name="model_provider_connection_status"
        ),
        sa.CheckConstraint(
            "last_probe_status IN ('never', 'running', 'ok', 'failed', 'needs_attention')",
            name="model_provider_connection_probe_status",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_model_provider_connections_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["workspace_credentials.id"],
            ondelete="RESTRICT",
            name=op.f("fk_model_provider_connections_credential_id_workspace_credentials"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_model_provider_connections_workspace_id_workspaces"),
        ),
    )
    op.create_index(
        op.f("ix_model_provider_connections_workspace_id"),
        "model_provider_connections",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_model_provider_connections_status"),
        "model_provider_connections",
        ["status"],
        unique=False,
    )
    op.create_index(
        "uq_model_provider_active_name",
        "model_provider_connections",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_model_provider_default",
        "model_provider_connections",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "workspace_notifications",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("notification_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_notifications")),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="workspace_notification_severity",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_notifications_recipient_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_workspace_notifications_workspace_id_workspaces"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "recipient_user_id",
            "source_type",
            "source_id",
            "notification_type",
            name="uq_workspace_notification_source_recipient",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_workspace_notifications_recipient_user_id"),
        "workspace_notifications",
        ["recipient_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_notifications_recipient_created",
        "workspace_notifications",
        ["workspace_id", "recipient_user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "notification_preferences",
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
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_preferences")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_notification_preferences_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="RESTRICT",
            name=op.f("fk_notification_preferences_workspace_id_workspaces"),
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name="uq_notification_preferences_scope"
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_table("workspace_notifications")
    op.drop_table("model_provider_connections")
    op.drop_table("workspace_capacity_reservations")
    op.drop_table("workspace_capacity_accounts")
    op.drop_table("workspace_credentials")
    op.drop_table("project_invitations")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("workspace_invitations")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
