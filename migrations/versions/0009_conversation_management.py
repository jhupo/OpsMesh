"""Separate durable conversation operations from ordered message delivery."""

import sqlalchemy as sa
from alembic import context, op

revision = "0009_conversation_management"
down_revision = "0008_channel_targets"
branch_labels = None
depends_on = None


def migrate_payloads() -> None:
    from opsmesh.config import get_settings
    from opsmesh.shared.security.encryption import SecretCipher
    from opsmesh.shared.serialization import payload_digest

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
    for channel in rows:
        settings_payload = cipher.decrypt_payload(
            channel["encrypted_settings"], key_id=channel["key_id"]
        )
        policy = settings_payload.get("policy", {})
        if not isinstance(policy, dict) or not isinstance(policy.get("groups", {}), dict):
            raise ValueError("Stored gateway policy must contain a groups object")
        groups = [policy.get("group", {}), *policy.get("groups", {}).values()]
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("Stored group policies must be objects")
            allowed = group.pop("allow_recall", False)
            if allowed:
                group["management"] = {
                    "actions": ["recall"],
                    "grants": {
                        identifier: {"actions": ["recall"], "automatic": True}
                        for identifier in policy.get("message_filters", [])
                    },
                }
        encrypted = cipher.encrypt_payload(settings_payload)
        connection.execute(
            sa.text(
                "UPDATE gateway_channels SET encrypted_settings=:payload, key_id=:key, revision=revision+1 WHERE id=:id"
            ),
            {"id": channel["id"], "payload": encrypted.ciphertext, "key": encrypted.key_id},
        )
        rejection_policy = payload_digest(
            {
                "agent_id": str(policy.get("rejection_agent_id")),
                "timeout": policy.get("rejection_timeout_seconds", 20),
            }
        )
        for receipt in connection.execute(
            sa.text(
                "SELECT id, encrypted_context FROM gateway_inbox WHERE channel_id=:channel AND encrypted_context IS NOT NULL"
            ),
            {"channel": channel["id"]},
        ).mappings():
            value = cipher.decrypt_payload(receipt["encrypted_context"])
            admission = value.get("admission")
            if not isinstance(admission, dict):
                continue
            if admission.get("action") == "recall":
                admission["action"] = "drop"
            admission["operations"] = []
            value["rejection_policy"] = rejection_policy
            encrypted_context = cipher.encrypt_payload(value)
            connection.execute(
                sa.text("UPDATE gateway_inbox SET encrypted_context=:payload WHERE id=:id"),
                {"id": receipt["id"], "payload": encrypted_context.ciphertext},
            )


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM gateway_channels) THEN RAISE EXCEPTION 'Conversation management requires an online encrypted-data migration'; END IF; END $$"
        )
    elif op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM gateway_inbox WHERE status IN ('pending','ready','dispatching','submitted','responding','batched')) OR EXISTS (SELECT 1 FROM gateway_deliveries WHERE status IN ('pending','sending','uncertain'))"
        )
    ):
        raise RuntimeError("Stop ingress and drain gateway work before upgrading to 0009")
    op.create_table(
        "gateway_management_operations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("channel_id", sa.UUID(), sa.ForeignKey("gateway_channels.id"), nullable=False),
        sa.Column("actor_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("resource_id", sa.String(200)),
        sa.Column("generation", sa.String(64)),
        sa.Column("source_id", sa.UUID()),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("adapter_generation", sa.String(64), nullable=False),
        sa.Column("channel_revision", sa.Integer(), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.UUID(), sa.ForeignKey("users.id")),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encrypted_result", sa.Text()),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(100)),
    )
    op.create_index(
        "ix_gateway_management_operations_channel_id",
        "gateway_management_operations",
        ["channel_id"],
    )
    op.create_index(
        "ix_gateway_management_queue",
        "gateway_management_operations",
        ["channel_id", "status", "created_at"],
    )
    op.create_table(
        "gateway_conversation_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("channel_id", sa.UUID(), sa.ForeignKey("gateway_channels.id"), nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("channel_revision", sa.Integer(), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.UniqueConstraint("channel_id", "event_key"),
    )
    op.create_index(
        "ix_gateway_conversation_events_channel_id", "gateway_conversation_events", ["channel_id"]
    )
    op.create_index(
        "ix_gateway_conversation_events_status", "gateway_conversation_events", ["status"]
    )
    # Keep the original encrypted moderation evidence without loading it in the runtime.
    op.create_table(
        "gateway_admission_history",
        sa.Column("inbox_id", sa.UUID(), primary_key=True),
        sa.Column("encrypted_context", sa.Text(), nullable=False),
    )
    op.execute(
        "INSERT INTO gateway_admission_history SELECT id, encrypted_context FROM gateway_inbox WHERE encrypted_context IS NOT NULL"
    )
    op.execute(
        "UPDATE gateway_deliveries SET status='discarded' WHERE kind='recall' AND status NOT IN ('sent','discarded')"
    )
    if not context.is_offline_mode():
        migrate_payloads()


def downgrade() -> None:
    raise RuntimeError(
        "Management effects cannot be reconstructed as message deliveries; restore the pre-upgrade snapshot"
    )
