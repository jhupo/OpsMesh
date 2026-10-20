"""Channel receipt and epoch invariants against real PostgreSQL."""

from uuid import uuid4

import pytest

from opsmesh.capabilities.models import CapabilityDefinition, CapabilityVersion
from opsmesh.channels import service
from opsmesh.channels.models import ChannelAccount, ChannelDelivery
from opsmesh.extensions.models import Installation
from opsmesh.identity.users.service import create_user
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher

CIPHER = SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")


@pytest.fixture
def channel(database_sessions):
    with database_sessions() as session, session.begin():
        user = create_user(session, email="channel@example.com", display_name="Channel")
        definition = CapabilityDefinition(
            type="channel", key="test-channel", name="Channel", created_by_user_id=user.id
        )
        session.add(definition)
        session.flush()
        version = CapabilityVersion(definition_id=definition.id, version=1, status="published")
        session.add(version)
        session.flush()
        installation = Installation(
            version_id=version.id,
            requested_by=user.id,
            package_digest="a" * 64,
            namespace="channel",
            status="enabled",
        )
        session.add(installation)
        session.flush()
        account = ChannelAccount(
            owner_id=user.id,
            channel_type="test",
            external_account_id="bot",
            installation_id=installation.id,
            version_id=version.id,
        )
        session.add(account)
        session.flush()
        return account.id


def test_inbox_deduplicates_and_rejects_changed_content(database_sessions, channel):
    args = dict(
        account_id=channel,
        epoch=1,
        external_message_id="one",
        conversation_key="private:one",
        payload={"text": "hello"},
        cipher=CIPHER,
    )
    with database_sessions() as session, session.begin():
        inbox, accepted = service.persist_inbox(session, **args)
        inbox_id = inbox.id
        assert accepted
        assert len(inbox.payload_digest) == 64
    with database_sessions() as session, session.begin():
        replay, accepted = service.persist_inbox(session, **args)
        assert replay.id == inbox_id and not accepted
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        service.persist_inbox(session, **{**args, "payload": {"text": "changed"}})


def test_rotated_epoch_rejects_ingress_and_retains_unsent_intent(database_sessions, channel):
    with database_sessions() as session, session.begin():
        delivery = service.reserve_delivery(
            session,
            account_id=channel,
            idempotency_key="one",
            conversation_key="group:one",
            payload={},
            cipher=CIPHER,
        )
        delivery_id = delivery.id
        service.change_connection(
            session,
            account_id=channel,
            expected_epoch=1,
            enabled=True,
        )
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        service.persist_inbox(
            session,
            account_id=channel,
            epoch=1,
            external_message_id="old",
            conversation_key="group:one",
            payload={},
            cipher=CIPHER,
        )
    with database_sessions() as session, session.begin():
        claimed = service.claim_delivery(session, delivery_id=delivery_id)
        assert claimed.status == "needs_attention" and claimed.attempts == 0


def test_unknown_send_is_not_reclaimed(database_sessions, channel):
    with database_sessions() as session, session.begin():
        delivery = service.reserve_delivery(
            session,
            account_id=channel,
            idempotency_key=str(uuid4()),
            conversation_key="thread:one",
            payload={},
            cipher=CIPHER,
        )
        delivery_id = delivery.id
        claimed = service.claim_delivery(session, delivery_id=delivery_id)
        assert claimed.status == "sending" and claimed.attempts == 1
        service.record_delivery_result(session, delivery_id=delivery_id, status="needs_attention")
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        service.claim_delivery(session, delivery_id=delivery_id)


def test_preloaded_delivery_cannot_be_claimed_twice(database_sessions, channel):
    with database_sessions() as session, session.begin():
        delivery_id = service.reserve_delivery(
            session,
            account_id=channel,
            idempotency_key="race",
            conversation_key="one",
            payload={},
            cipher=CIPHER,
        ).id
    with database_sessions() as stale:
        cached = stale.get(ChannelDelivery, delivery_id)
        assert cached.status == "pending"
        with database_sessions() as other, other.begin():
            service.claim_delivery(other, delivery_id=delivery_id)
        with pytest.raises(ConflictError):
            service.claim_delivery(stale, delivery_id=delivery_id)
        stale.rollback()
    with database_sessions() as session:
        assert session.get(ChannelDelivery, delivery_id).attempts == 1


def test_preloaded_account_rejects_revoked_epoch(database_sessions, channel):
    with database_sessions() as stale:
        cached = stale.get(ChannelAccount, channel)
        assert cached.epoch == 1
        with database_sessions() as other, other.begin():
            service.change_connection(
                other,
                account_id=channel,
                expected_epoch=1,
                enabled=True,
            )
        with pytest.raises(ConflictError):
            service.persist_inbox(
                stale,
                account_id=channel,
                epoch=1,
                external_message_id="stale",
                conversation_key="one",
                payload={},
                cipher=CIPHER,
            )


def test_preloaded_result_cannot_overwrite_confirmed_send(database_sessions, channel):
    with database_sessions() as session, session.begin():
        delivery_id = service.reserve_delivery(
            session,
            account_id=channel,
            idempotency_key="result",
            conversation_key="one",
            payload={},
            cipher=CIPHER,
        ).id
        service.claim_delivery(session, delivery_id=delivery_id)
    with database_sessions() as stale:
        cached = stale.get(ChannelDelivery, delivery_id)
        assert cached.status == "sending"
        with database_sessions() as other, other.begin():
            service.record_delivery_result(
                other,
                delivery_id=delivery_id,
                status="sent",
                external_message_id="vendor-one",
            )
        with pytest.raises(ConflictError):
            service.record_delivery_result(stale, delivery_id=delivery_id, status="failed")
        stale.rollback()
    with database_sessions() as session:
        assert session.get(ChannelDelivery, delivery_id).external_message_id == "vendor-one"
