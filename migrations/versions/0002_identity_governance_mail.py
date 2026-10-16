"""identity, governance and platform mail facts

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16 00:00:00.000000+00:00

S4 tables: identity (users, credentials, sessions, quotas, invitations),
governance (audit chain, approvals, security events) and platform mail. The
audit chain head singleton is seeded here; appends also create it lazily on a
database built by create_all. No historical architecture migration is
inherited.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GENESIS_HASH = "0" * 64


def upgrade() -> None:
    op.create_table(
        "users",
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
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("platform_roles", postgresql.JSONB(), nullable=False),
        sa.Column("work_root", sa.String(length=500), nullable=True),
        sa.Column("avatar_content", sa.LargeBinary(), nullable=True),
        sa.Column("avatar_content_type", sa.String(length=50), nullable=True),
        sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_table(
        "user_credentials",
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
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_credentials")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_credentials_user_id_users")
        ),
    )
    op.create_index(
        op.f("ix_user_credentials_user_id"), "user_credentials", ["user_id"], unique=True
    )
    op.create_table(
        "login_sessions",
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
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_sessions")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_login_sessions_user_id_users")
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_sessions_token_hash")),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"], unique=False)
    op.create_table(
        "user_quota_limits",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_limit", sa.BigInteger(), nullable=True),
        sa.Column("storage_limit_bytes", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_quota_limits")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_quota_limits_user_id_users")
        ),
    )
    op.create_table(
        "user_token_accounts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_token_accounts")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_token_accounts_user_id_users")
        ),
    )
    op.create_table(
        "user_storage_accounts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_storage_accounts")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_storage_accounts_user_id_users")
        ),
    )
    op.create_table(
        "token_reservations",
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
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("settled_amount", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_scope", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_token_reservations")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_token_reservations_user_id_users")
        ),
        sa.UniqueConstraint(
            "idempotency_scope",
            "idempotency_key",
            name=op.f("uq_token_reservations_scope_key"),
        ),
    )
    op.create_index(
        op.f("ix_token_reservations_user_id"), "token_reservations", ["user_id"], unique=False
    )
    op.create_table(
        "storage_reservations",
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
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("settled_amount", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_scope", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_reservations")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_storage_reservations_user_id_users")
        ),
        sa.UniqueConstraint(
            "idempotency_scope",
            "idempotency_key",
            name=op.f("uq_storage_reservations_scope_key"),
        ),
    )
    op.create_index(
        op.f("ix_storage_reservations_user_id"),
        "storage_reservations",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "account_invitations",
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
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", sa.Uuid(), nullable=True),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_account_invitations_token_hash")),
    )
    op.create_index(
        op.f("ix_account_invitations_email"), "account_invitations", ["email"], unique=False
    )
    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain_head")),
    )
    # The seed uses SQL text: a bound parameter cannot carry a server-side
    # now(), and the appends create the row lazily on a create_all database.
    op.execute(
        sa.text(
            "INSERT INTO audit_chain_head (id, last_sequence, last_hash, updated_at) "
            "VALUES (:id, :last_sequence, :last_hash, now())"
        ).bindparams(id=1, last_sequence=0, last_hash=GENESIS_HASH)
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("installation_id", sa.Uuid(), nullable=True),
        sa.Column("instance_id", sa.Uuid(), nullable=True),
        sa.Column("invocation_id", sa.Uuid(), nullable=True),
        sa.Column("package_key", sa.String(length=100), nullable=True),
        sa.Column("configuration_revision", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=200), nullable=False),
        sa.Column("target_type", sa.String(length=200), nullable=False),
        sa.Column("target_id", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=2000), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("sequence", name=op.f("uq_audit_events_sequence")),
        sa.UniqueConstraint("event_hash", name=op.f("uq_audit_events_event_hash")),
    )
    op.create_table(
        "audit_append_receipts",
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
        sa.Column("idempotency_scope", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_append_receipts")),
        sa.UniqueConstraint(
            "idempotency_scope",
            "idempotency_key",
            name=op.f("uq_audit_receipts_scope_key"),
        ),
    )
    op.create_table(
        "audit_verifications",
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
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("from_sequence", sa.BigInteger(), nullable=False),
        sa.Column("to_sequence", sa.BigInteger(), nullable=False),
        sa.Column("checked_count", sa.BigInteger(), nullable=False),
        sa.Column("first_broken_sequence", sa.BigInteger(), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_verifications")),
    )
    op.create_table(
        "approval_requests",
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
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=2000), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=False),
        sa.Column("target_id", sa.String(length=200), nullable=False),
        sa.Column("target_version", sa.String(length=100), nullable=True),
        sa.Column("approver_user_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=True),
        sa.Column("requester_installation_id", sa.Uuid(), nullable=True),
        sa.Column("requester_invocation_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decision_idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        sa.UniqueConstraint(
            "kind",
            "target_type",
            "target_id",
            name=op.f("uq_approval_requests_target"),
        ),
    )
    op.create_table(
        "security_events",
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
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_events")),
    )
    op.create_index(op.f("ix_security_events_kind"), "security_events", ["kind"], unique=False)
    op.create_index(
        op.f("ix_security_events_user_id"), "security_events", ["user_id"], unique=False
    )
    op.create_table(
        "smtp_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("security", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_ciphertext", sa.Text(), nullable=True),
        sa.Column("password_key_id", sa.String(length=64), nullable=True),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("from_name", sa.String(length=100), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_smtp_configurations")),
    )
    op.create_table(
        "mail_intents",
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
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body_ciphertext", sa.Text(), nullable=False),
        sa.Column("body_key_id", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_scope", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_unknown", sa.Boolean(), nullable=False),
        sa.Column("failure_reason", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reconciled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_intents")),
        sa.UniqueConstraint(
            "idempotency_scope", "idempotency_key", name=op.f("uq_mail_intents_scope_key")
        ),
    )


def downgrade() -> None:
    op.drop_table("mail_intents")
    op.drop_table("smtp_configurations")
    op.drop_index(op.f("ix_security_events_user_id"), table_name="security_events")
    op.drop_index(op.f("ix_security_events_kind"), table_name="security_events")
    op.drop_table("security_events")
    op.drop_table("approval_requests")
    op.drop_table("audit_verifications")
    op.drop_table("audit_append_receipts")
    op.drop_table("audit_events")
    op.drop_table("audit_chain_head")
    op.drop_index(op.f("ix_account_invitations_email"), table_name="account_invitations")
    op.drop_table("account_invitations")
    op.drop_index(
        op.f("ix_storage_reservations_user_id"), table_name="storage_reservations"
    )
    op.drop_table("storage_reservations")
    op.drop_index(op.f("ix_token_reservations_user_id"), table_name="token_reservations")
    op.drop_table("token_reservations")
    op.drop_table("user_storage_accounts")
    op.drop_table("user_token_accounts")
    op.drop_table("user_quota_limits")
    op.drop_index("ix_login_sessions_user_id", table_name="login_sessions")
    op.drop_table("login_sessions")
    op.drop_index(op.f("ix_user_credentials_user_id"), table_name="user_credentials")
    op.drop_table("user_credentials")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
