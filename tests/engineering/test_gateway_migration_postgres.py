"""Upgrade encrypted 0.1.1 receipts in an isolated, disposable schema."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, insert, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from opsmesh.shared.security.encryption import SecretCipher


@pytest.fixture
def legacy_gateway(database_url, monkeypatch):
    schema = "gateway_upgrade_" + uuid4().hex
    control = create_engine(database_url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url)
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    settings = SimpleNamespace(
        credential_encryption_key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=",
        credential_encryption_key_id="migration-test",
        credential_encryption_previous_keys={},
    )
    monkeypatch.setattr("opsmesh.config.get_settings", lambda: settings)
    cipher = SecretCipher(
        key=settings.credential_encryption_key, key_id=settings.credential_encryption_key_id
    )
    try:
        command.upgrade(config, "0006_gateway_processing")
        yield engine, config, cipher
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()


@pytest.mark.parametrize("status", ["pending", "completed", "submitted"])
def test_upgrade_reencrypts_policy_and_preserves_original_receipts(legacy_gateway, status):
    engine, config, cipher = legacy_gateway
    metadata = MetaData()
    users = Table("users", metadata, autoload_with=engine)
    channels = Table("gateway_channels", metadata, autoload_with=engine)
    identities = Table("gateway_identities", metadata, autoload_with=engine)
    receipts = Table("gateway_inbox", metadata, autoload_with=engine)
    user, channel, identity, parent, child = (uuid4() for _ in range(5))
    encrypted = cipher.encrypt_payload(
        {
            "config": {},
            "policy": {
                "group": {
                    "history_scope": "group",
                    "history_limit": 10,
                    "history_ttl_seconds": 300,
                    "clear_after_reply": True,
                },
                "groups": {"private-history": {"history_scope": "user", "history_limit": 5}},
            },
        }
    )
    original = cipher.encrypt_payload({"original": "retained"})
    context = cipher.encrypt_payload({"batched_ids": [str(child)]})
    with engine.begin() as connection:
        connection.execute(
            insert(users).values(
                id=user,
                email="review@example.com",
                display_name="Review",
                status="active",
                platform_roles=[],
            )
        )
        connection.execute(
            insert(channels).values(
                id=channel,
                owner_id=user,
                kind="qq",
                name="Review",
                enabled=False,
                revision=1,
                deleted=False,
                send_window_count=0,
                encrypted_settings=encrypted.ciphertext,
                key_id=encrypted.key_id,
            )
        )
        connection.execute(
            insert(identities).values(
                id=identity,
                channel_id=channel,
                identity_key="a" * 64,
                scope="app",
                id_kind="member_openid",
                external_id="member",
                encrypted_profile=original.ciphertext,
                key_id=original.key_id,
            )
        )
        for identifier, state, batch in (
            (parent, status, context.ciphertext),
            (child, "batched", None),
        ):
            connection.execute(
                insert(receipts).values(
                    id=identifier,
                    channel_id=channel,
                    identity_id=identity,
                    event_key=identifier.hex,
                    conversation_key="b" * 64,
                    status=state,
                    encrypted_payload=original.ciphertext,
                    key_id=original.key_id,
                    encrypted_context=batch,
                    event_cursor=0,
                )
            )
    if status == "submitted":
        with pytest.raises(RuntimeError, match="Drain"):
            command.upgrade(config, "0007_gateway_admission")
        assert "queue_key" not in {
            column["name"] for column in inspect(engine).get_columns("gateway_inbox")
        }
        with engine.begin() as connection:
            connection.execute(
                receipts.update().where(receipts.c.id == parent).values(status="completed")
            )
    command.upgrade(config, "0007_gateway_admission")
    with engine.connect() as connection:
        settings = connection.execute(select(channels.c.encrypted_settings)).scalar_one()
        policy = cipher.decrypt_payload(settings)["policy"]
        assert policy["group"] == {"session_scope": "group"}
        assert policy["groups"]["private-history"] == {"session_scope": "user"}
        rows = {row.id: row for row in connection.execute(select(receipts)).mappings()}
        assert rows[child]["status"] == ("pending" if status == "pending" else "completed")
        assert all(row["encrypted_payload"] == original.ciphertext for row in rows.values())


@pytest.mark.parametrize(
    "targets,pending,kind,personal,private_enabled",
    [
        (0, False, "group", False, None),
        (1, False, "group", False, None),
        (2, False, "group", False, None),
        (1, True, "group", False, None),
        (1, False, "guild", False, None),
        (1, False, "private", True, True),
        (1, False, "group", True, False),
        (1, False, "group", False, True),
    ],
)
def test_channel_target_upgrade_retains_evidence_and_disables_ambiguous_channels(
    legacy_gateway, targets, pending, kind, personal, private_enabled
):
    engine, config, cipher = legacy_gateway
    command.upgrade(config, "0007_gateway_admission")
    metadata = MetaData()
    tables = {
        name: Table(name, metadata, autoload_with=engine)
        for name in (
            "users",
            "gateway_channels",
            "gateway_identities",
            "gateway_account_bindings",
            "gateway_routes",
            "gateway_conversations",
            "gateway_inbox",
        )
    }
    user, channel, identity, target, receipt, route = (uuid4() for _ in range(6))
    settings = cipher.encrypt_payload(
        {
            "config": {},
            "policy": {
                **({"private_enabled": private_enabled} if private_enabled is not None else {}),
                "group": {"session_scope": "group"},
                "groups": {"allowed": {"session_scope": "group"}, "unused": {}},
            },
        }
    )
    original = cipher.encrypt_payload({"original": "retained"})
    batch = cipher.encrypt_payload(
        {
            "batch": [
                {
                    "inbox_id": str(receipt),
                    "identity_id": str(identity),
                    "message": {"original": "retained"},
                    "route": {"id": str(route)},
                }
            ]
        }
    )
    with engine.begin() as connection:
        connection.execute(
            insert(tables["users"]).values(
                id=user,
                email="channel@example.com",
                display_name="Channel",
                status="active",
                platform_roles=[],
            )
        )
        connection.execute(
            insert(tables["gateway_channels"]).values(
                id=channel,
                owner_id=user,
                kind="qq",
                name="Channel",
                enabled=True,
                revision=1,
                deleted=False,
                send_window_count=0,
                encrypted_settings=settings.ciphertext,
                key_id=settings.key_id,
            )
        )
        connection.execute(
            insert(tables["gateway_identities"]).values(
                id=identity,
                channel_id=channel,
                identity_key="a" * 64,
                scope="app",
                id_kind="member_openid",
                external_id="member",
                encrypted_profile=original.ciphertext,
                key_id=original.key_id,
            )
        )
        if personal:
            connection.execute(
                insert(tables["gateway_account_bindings"]).values(
                    identity_id=identity, user_id=user, verified_by=user
                )
            )
        for index in range(targets):
            key = str(index) * 64
            connection.execute(
                insert(tables["gateway_conversations"]).values(
                    channel_id=channel,
                    conversation_key=key,
                    external_id="allowed" if index == 0 else "second",
                    kind=kind,
                    encrypted_profile=original.ciphertext,
                    key_id=original.key_id,
                )
            )
            connection.execute(
                insert(tables["gateway_routes"]).values(
                    id=route if index == 0 else uuid4(),
                    channel_id=channel,
                    identity_id=identity if personal else None,
                    conversation_key=None if personal else key,
                    conversation_id="allowed" if personal and kind == "group" else "",
                    user_id=None if personal else user,
                    target_kind="agent",
                    target_id=target if index == 0 else uuid4(),
                    output_mode="message",
                    allow_group=True,
                    enabled=True,
                    revision=1,
                )
            )
        if targets:
            connection.execute(
                insert(tables["gateway_inbox"]).values(
                    id=receipt,
                    channel_id=channel,
                    identity_id=identity,
                    event_key="event",
                    conversation_key="0" * 64,
                    status="pending" if pending else "completed",
                    route_id=route,
                    route_revision=1,
                    user_id=user,
                    encrypted_payload=original.ciphertext,
                    key_id=original.key_id,
                    encrypted_context=batch.ciphertext,
                    event_cursor=0,
                    filter_attempts=0,
                )
            )
    if pending:
        with pytest.raises(RuntimeError, match="drain"):
            command.upgrade(config, "head")
        assert "binding_revision" not in {
            column["name"] for column in inspect(engine).get_columns("gateway_channels")
        }
        with engine.begin() as connection:
            connection.execute(tables["gateway_inbox"].update().values(status="completed"))
    command.upgrade(config, "head")
    metadata = MetaData()
    channels = Table("gateway_channels", metadata, autoload_with=engine)
    receipts = Table("gateway_inbox", metadata, autoload_with=engine)
    archives = Table("gateway_route_receipts", metadata, autoload_with=engine)
    with engine.connect() as connection:
        row = connection.execute(select(channels)).mappings().one()
        value = cipher.decrypt_payload(row["encrypted_settings"])
        assert row["enabled"] == (targets == 1 and not personal)
        assert row["binding_revision"] == 1
        assert value["policy"]["group"] == {}
        assert value["policy"]["private_enabled"] is False
        assert "unused" not in value["policy"]["groups"]
        if targets == 1:
            assert value["target"] == {
                "user_id": str(user),
                "target_kind": "agent",
                "target_id": str(target),
                "output_mode": "message",
            }
            assert value["policy"]["groups"] == ({} if personal else {"allowed": {}})
        else:
            assert value["target"] is None
        assert len(connection.execute(select(tables["gateway_routes"])).all()) == targets
        if targets:
            row = connection.execute(select(receipts)).mappings().one()
            assert row["encrypted_payload"] == original.ciphertext
            assert row["target_id"] == target and row["target_kind"] == "agent"
            assert row["binding_revision"] == (1 if targets == 1 and not personal else 0)
            assert "route" not in cipher.decrypt_payload(row["encrypted_context"])["batch"][0]
            archived = connection.execute(select(archives)).mappings().one()
            assert archived["route_id"] == route and archived["route_revision"] == 1
            assert archived["encrypted_context"] == batch.ciphertext


def test_management_cutover_archives_moderation_and_retires_unsent_recalls(legacy_gateway):
    engine, config, cipher = legacy_gateway
    command.upgrade(config, "0008_channel_targets")
    metadata = MetaData()
    tables = {
        name: Table(name, metadata, autoload_with=engine)
        for name in (
            "users",
            "gateway_channels",
            "gateway_identities",
            "gateway_inbox",
            "gateway_deliveries",
        )
    }
    user, channel, identity, receipt, delivery, target = (uuid4() for _ in range(6))
    resource = "review/message_filter/check"
    settings = cipher.encrypt_payload(
        {
            "config": {},
            "target": {
                "user_id": str(user),
                "target_kind": "agent",
                "target_id": str(target),
                "output_mode": "text",
            },
            "policy": {"groups": {"group": {"allow_recall": True}}, "message_filters": [resource]},
        }
    )
    original = cipher.encrypt_payload(
        {"admission": {"action": "recall", "reason": "spam", "rejection_input": "Refuse safely."}}
    )
    payload = cipher.encrypt_payload({"event": "recall", "content": []})
    with engine.begin() as connection:
        connection.execute(
            insert(tables["users"]).values(
                id=user,
                email="management-upgrade@example.com",
                display_name="Upgrade",
                status="active",
                platform_roles=[],
            )
        )
        connection.execute(
            insert(tables["gateway_channels"]).values(
                id=channel,
                owner_id=user,
                kind="qq",
                name="Upgrade",
                enabled=False,
                revision=1,
                binding_revision=1,
                deleted=False,
                send_window_count=0,
                encrypted_settings=settings.ciphertext,
                key_id=settings.key_id,
            )
        )
        connection.execute(
            insert(tables["gateway_identities"]).values(
                id=identity,
                channel_id=channel,
                identity_key="i" * 64,
                scope="app",
                id_kind="member_openid",
                external_id="member",
                encrypted_profile=payload.ciphertext,
                key_id=payload.key_id,
            )
        )
        connection.execute(
            insert(tables["gateway_inbox"]).values(
                id=receipt,
                channel_id=channel,
                identity_id=identity,
                event_key="event",
                conversation_key="c" * 64,
                status="completed",
                user_id=user,
                target_id=target,
                target_kind="agent",
                binding_revision=1,
                encrypted_payload=payload.ciphertext,
                key_id=payload.key_id,
                encrypted_context=original.ciphertext,
                event_cursor=0,
                filter_attempts=0,
            )
        )
        connection.execute(
            insert(tables["gateway_deliveries"]).values(
                id=delivery,
                channel_id=channel,
                inbox_id=receipt,
                ordinal=1,
                kind="recall",
                status="failed",
                attempts=1,
                encrypted_payload=payload.ciphertext,
                key_id=payload.key_id,
            )
        )
    command.upgrade(config, "head")
    history = Table("gateway_admission_history", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        channel_row = connection.execute(select(tables["gateway_channels"])).mappings().one()
        group = cipher.decrypt_payload(channel_row["encrypted_settings"])["policy"]["groups"][
            "group"
        ]
        assert "allow_recall" not in group
        assert group["management"] == {
            "actions": ["recall"],
            "grants": {resource: {"actions": ["recall"], "automatic": True}},
        }
        row = connection.execute(select(tables["gateway_inbox"])).mappings().one()
        assert cipher.decrypt_payload(row["encrypted_context"])["admission"]["action"] == "drop"
        assert (
            connection.execute(select(history.c.encrypted_context)).scalar_one()
            == original.ciphertext
        )
        row = connection.execute(select(tables["gateway_deliveries"])).mappings().one()
        assert row["status"] == "discarded" and row["encrypted_payload"] == payload.ciphertext
    assert {"gateway_management_operations", "gateway_conversation_events"}.issubset(
        inspect(engine).get_table_names()
    )
