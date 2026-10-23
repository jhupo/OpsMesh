"""Individual ingress admission and principal-scoped conversation batches."""

from uuid import UUID

import sqlalchemy as sa
from alembic import context, op

revision = "0007_gateway_admission"
down_revision = "0006_gateway_processing"
branch_labels = None
depends_on = None


def migrate_settings() -> None:
    from opsmesh.config import get_settings
    from opsmesh.shared.security.encryption import SecretCipher

    connection = op.get_bind()
    rows = (
        connection.execute(sa.text("SELECT id, encrypted_settings, key_id FROM gateway_channels"))
        .mappings()
        .all()
    )
    if not rows:
        return
    settings = get_settings()
    cipher = SecretCipher(
        key=settings.credential_encryption_key,
        key_id=settings.credential_encryption_key_id,
        previous_keys=settings.credential_encryption_previous_keys,
    )
    for row in rows:
        value = cipher.decrypt_payload(row["encrypted_settings"], key_id=row["key_id"])
        policy = value.get("policy", {})
        if not isinstance(policy, dict):
            raise ValueError("Stored gateway policy must be an object")
        groups = policy.get("groups", {})
        if not isinstance(groups, dict):
            raise ValueError("Stored gateway groups must be an object")
        for group in [policy.get("group", {}), *groups.values()]:
            if not isinstance(group, dict):
                raise ValueError("Stored group policy must be an object")
            group["session_scope"] = group.pop("history_scope", "group")
            if group.get("visibility") in {"auto", "mention_recent"}:
                group["visibility"] = "mention_only"
            for key in ("history_limit", "history_ttl_seconds", "clear_after_reply"):
                group.pop(key, None)
        encrypted = cipher.encrypt_payload(value)
        connection.execute(
            sa.text(
                "UPDATE gateway_channels SET encrypted_settings=:payload, key_id=:key WHERE id=:id"
            ),
            {"payload": encrypted.ciphertext, "key": encrypted.key_id, "id": row["id"]},
        )
    # Old batches did not have per-message admission. Re-admit unfinished children;
    # preserve all original receipts and native session files.
    for row in connection.execute(
        sa.text(
            "SELECT id, status, run_id, encrypted_context FROM gateway_inbox "
            "WHERE encrypted_context IS NOT NULL"
        )
    ).mappings():
        context = cipher.decrypt_payload(row["encrypted_context"])
        identifiers = context.get("batched_ids", [])
        if not isinstance(identifiers, list):
            raise ValueError("Stored gateway batch IDs must be an array")
        status = (
            "completed"
            if row["status"] == "completed"
            else "blocked"
            if row["run_id"]
            else "pending"
        )
        for identifier in identifiers:
            connection.execute(
                sa.text(
                    "UPDATE gateway_inbox SET status=:status, run_id=:run_id "
                    "WHERE id=:id AND status='batched'"
                ),
                {"id": UUID(str(identifier)), "status": status, "run_id": row["run_id"]},
            )


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM gateway_channels) THEN "
            "RAISE EXCEPTION 'Gateway data requires an online encrypted-data migration'; "
            "END IF; END $$"
        )
    else:
        if op.get_bind().scalar(
            sa.text("SELECT count(*) FROM gateway_inbox WHERE status='submitted'")
        ):
            raise RuntimeError(
                "Drain submitted gateway runs before upgrading to 0007_gateway_admission"
            )
        migrate_settings()
    op.execute(
        "UPDATE gateway_inbox SET status='completed' "
        "WHERE status IN ('passive', 'context_consumed')"
    )
    op.alter_column("gateway_routes", "identity_id", nullable=True)
    op.add_column("gateway_routes", sa.Column("conversation_key", sa.String(64)))
    op.add_column("gateway_routes", sa.Column("user_id", sa.UUID()))
    op.create_foreign_key(
        "fk_gateway_routes_user_id_users", "gateway_routes", "users", ["user_id"], ["id"]
    )
    op.create_unique_constraint(
        "uq_gateway_routes_channel_id", "gateway_routes", ["channel_id", "conversation_key"]
    )
    op.create_check_constraint(
        op.f("ck_gateway_routes_subject"),
        "gateway_routes",
        "(identity_id IS NOT NULL AND conversation_key IS NULL AND user_id IS NULL) OR "
        "(identity_id IS NULL AND conversation_key IS NOT NULL AND user_id IS NOT NULL "
        "AND allow_group AND conversation_id = '')",
    )
    op.add_column("gateway_sessions", sa.Column("scope_key", sa.String(128)))
    op.execute("UPDATE gateway_sessions SET scope_key=route_id::text || ':' || conversation_key")
    op.alter_column("gateway_sessions", "scope_key", nullable=False)
    op.drop_constraint("pk_gateway_sessions", "gateway_sessions", type_="primary")
    op.create_primary_key("pk_gateway_sessions", "gateway_sessions", ["scope_key"])
    op.drop_column("gateway_sessions", "route_id")
    op.drop_column("gateway_sessions", "conversation_key")
    op.add_column("gateway_inbox", sa.Column("queue_key", sa.String(64)))
    op.create_index("ix_gateway_inbox_queue_key", "gateway_inbox", ["queue_key"])
    op.add_column("gateway_inbox", sa.Column("filter_revision", sa.Integer()))
    op.add_column("gateway_inbox", sa.Column("filter_retry_at", sa.DateTime(timezone=True)))
    op.add_column(
        "gateway_inbox",
        sa.Column("filter_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("gateway_inbox", "filter_attempts", server_default=None)
    op.drop_index("ix_gateway_inbox_session_queue", table_name="gateway_inbox")
    op.drop_index("ix_gateway_inbox_group_history", table_name="gateway_inbox")
    op.create_index(
        "ix_gateway_inbox_conversation_queue",
        "gateway_inbox",
        ["channel_id", "conversation_key", "status", "created_at", "id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "This data migration cannot safely split shared sessions; restore the pre-upgrade snapshot"
    )
