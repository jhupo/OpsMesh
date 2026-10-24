"""Bind each channel to one executor and retain historical routing evidence."""

import logging

import sqlalchemy as sa
from alembic import context, op

revision = "0008_channel_targets"
down_revision = "0007_gateway_admission"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def migrate_bindings() -> None:
    from opsmesh.config import get_settings
    from opsmesh.shared.security.encryption import SecretCipher

    connection = op.get_bind()
    channels = connection.execute(sa.text("SELECT * FROM gateway_channels")).mappings().all()
    if not channels:
        return
    settings = get_settings()
    cipher = SecretCipher(
        key=settings.credential_encryption_key,
        key_id=settings.credential_encryption_key_id,
        previous_keys=settings.credential_encryption_previous_keys,
    )
    for channel in channels:
        routes = (
            connection.execute(
                sa.text(
                    "SELECT r.*, COALESCE(r.user_id, b.user_id) AS executor_id, "
                    "c.external_id AS group_id FROM gateway_routes r "
                    "LEFT JOIN gateway_account_bindings b ON b.identity_id=r.identity_id "
                    "LEFT JOIN gateway_conversations c ON c.channel_id=r.channel_id "
                    "AND c.conversation_key=r.conversation_key AND c.kind IN ('group','guild') "
                    "WHERE r.channel_id=:channel AND r.enabled"
                ),
                {"channel": channel["id"]},
            )
            .mappings()
            .all()
        )
        candidates = {
            (row["executor_id"], row["target_kind"], row["target_id"], row["output_mode"])
            for row in routes
        }
        target = None
        if len(candidates) == 1:
            user_id, kind, target_id, output_mode = next(iter(candidates))
            if user_id is not None:
                target = {
                    "user_id": str(user_id),
                    "target_kind": kind,
                    "target_id": str(target_id),
                    "output_mode": output_mode,
                }
        value = cipher.decrypt_payload(channel["encrypted_settings"], key_id=channel["key_id"])
        policy = value.get("policy", {})
        if not isinstance(policy, dict):
            raise ValueError("Stored gateway policy must be an object")
        groups = policy.get("groups", {})
        if not isinstance(groups, dict):
            raise ValueError("Stored gateway groups must be an object")
        default_group = policy.get("group", {})
        for group in [default_group, *groups.values()]:
            if not isinstance(group, dict):
                raise ValueError("Stored group policy must be an object")
            group.pop("session_scope", None)
        # Individual routes cannot become a public conversation authorization.
        # Private admission now applies to every sender, so it requires explicit
        # administrator opt-in even when the old policy enabled private traffic.
        personal_routes = any(row["identity_id"] is not None for row in routes)
        policy["private_enabled"] = False
        allowed_groups = {
            str(row["group_id"])
            for row in routes
            if row["identity_id"] is None and row["allow_group"] and row["group_id"]
        }
        policy["groups"] = {key: groups.get(key, dict(default_group)) for key in allowed_groups}
        value["policy"], value["target"] = policy, target
        encrypted = cipher.encrypt_payload(value)
        connection.execute(
            sa.text(
                "UPDATE gateway_channels SET encrypted_settings=:payload, key_id=:key, "
                "enabled=:enabled, revision=revision+1 WHERE id=:id"
            ),
            {
                "payload": encrypted.ciphertext,
                "key": encrypted.key_id,
                "enabled": bool(channel["enabled"] and target and not personal_routes),
                "id": channel["id"],
            },
        )
        if (target is None or personal_routes) and not channel["deleted"]:
            logger.warning(
                "Channel %s requires explicit target and conversation access review before enabling",
                channel["id"],
            )
        # A completed receipt can authorize a push only under the same target
        # and the route revision actually admitted at the time.
        if target is not None and not personal_routes:
            connection.execute(
                sa.text(
                    "UPDATE gateway_inbox i SET binding_revision=1 FROM gateway_routes r "
                    "WHERE i.route_id=r.id AND i.channel_id=:channel AND r.enabled "
                    "AND i.route_revision=r.revision AND i.user_id=:user "
                    "AND r.target_kind=:kind AND r.target_id=:target AND r.output_mode=:mode"
                ),
                {
                    "channel": channel["id"],
                    "user": user_id,
                    "kind": kind,
                    "target": target_id,
                    "mode": output_mode,
                },
            )
    for row in connection.execute(
        sa.text(
            "SELECT id, encrypted_context FROM gateway_inbox WHERE encrypted_context IS NOT NULL"
        )
    ).mappings():
        value = cipher.decrypt_payload(row["encrypted_context"])
        batch = value.get("batch", [])
        if not isinstance(batch, list):
            raise ValueError("Stored gateway batch must be an array")
        for item in batch:
            if not isinstance(item, dict):
                raise ValueError("Stored gateway batch entries must be objects")
            item.pop("route", None)
        encrypted = cipher.encrypt_payload(value)
        connection.execute(
            sa.text("UPDATE gateway_inbox SET encrypted_context=:payload WHERE id=:id"),
            {"id": row["id"], "payload": encrypted.ciphertext},
        )


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM gateway_channels) THEN "
            "RAISE EXCEPTION 'Channel targets require an online encrypted-data migration'; "
            "END IF; END $$"
        )
    else:
        connection = op.get_bind()
        if connection.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM gateway_inbox WHERE status IN "
                "('pending','ready','dispatching','submitted','responding','batched')) OR "
                "EXISTS (SELECT 1 FROM gateway_deliveries WHERE status IN ('pending','sending','uncertain'))"
            )
        ):
            raise RuntimeError(
                "Stop ingress and drain gateway messages/deliveries before upgrading to 0008"
            )
    op.add_column(
        "gateway_channels",
        sa.Column("binding_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("gateway_channels", "binding_revision", server_default=None)
    op.create_table(
        "gateway_route_receipts",
        sa.Column("inbox_id", sa.UUID(), primary_key=True),
        sa.Column("route_id", sa.UUID()),
        sa.Column("route_revision", sa.Integer()),
        sa.Column("encrypted_context", sa.Text()),
    )
    op.execute(
        "INSERT INTO gateway_route_receipts SELECT id, route_id, route_revision, encrypted_context "
        "FROM gateway_inbox WHERE route_id IS NOT NULL OR encrypted_context IS NOT NULL"
    )
    op.add_column("gateway_inbox", sa.Column("target_id", sa.UUID()))
    op.add_column("gateway_inbox", sa.Column("target_kind", sa.String(16)))
    op.add_column("gateway_inbox", sa.Column("binding_revision", sa.Integer()))
    op.execute(
        "UPDATE gateway_inbox i SET target_id=r.target_id, target_kind=r.target_kind, "
        "binding_revision=0 FROM gateway_routes r WHERE i.route_id=r.id"
    )
    if not context.is_offline_mode():
        migrate_bindings()
    op.drop_constraint(
        "fk_gateway_inbox_route_id_gateway_routes", "gateway_inbox", type_="foreignkey"
    )
    op.drop_column("gateway_inbox", "route_id")
    op.drop_column("gateway_inbox", "route_revision")


def downgrade() -> None:
    raise RuntimeError(
        "Channel targets cannot safely reconstruct per-identity routes; restore the pre-upgrade snapshot"
    )
