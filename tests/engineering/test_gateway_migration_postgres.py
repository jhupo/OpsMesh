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
            command.upgrade(config, "head")
        assert "queue_key" not in {
            column["name"] for column in inspect(engine).get_columns("gateway_inbox")
        }
        with engine.begin() as connection:
            connection.execute(
                receipts.update().where(receipts.c.id == parent).values(status="completed")
            )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        settings = connection.execute(select(channels.c.encrypted_settings)).scalar_one()
        policy = cipher.decrypt_payload(settings)["policy"]
        assert policy["group"] == {"session_scope": "group"}
        assert policy["groups"]["private-history"] == {"session_scope": "user"}
        rows = {row.id: row for row in connection.execute(select(receipts)).mappings()}
        assert rows[child]["status"] == ("pending" if status == "pending" else "completed")
        assert all(row["encrypted_payload"] == original.ciphertext for row in rows.values())
