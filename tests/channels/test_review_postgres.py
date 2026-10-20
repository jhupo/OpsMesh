"""Current scope, interrupted effects and drain regression checks over real PG/HTTP."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.channels import service
from opsmesh.channels.models import ChannelAccount, ChannelDelivery
from opsmesh.identity.users.models import UserProfile
from opsmesh.identity.users.service import load_identity
from opsmesh.orchestration.configurations.models import ConfigurationMember
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.errors import ConflictError, NotFoundError
from tests.channels.helpers import flow_configuration
from tests.channels.test_channels_postgres import CIPHER
from tests.channels.test_channels_postgres import channel as channel
from tests.execution.test_session_routes import app_client as app_client
from tests.execution.test_session_routes import register_account


@pytest.fixture
def owned_channel(database_sessions, channel):  # noqa: F811
    token = register_account(database_sessions, email="channel-review@example.com")
    with database_sessions() as session, session.begin():
        owner = session.scalar(
            select(UserProfile).where(UserProfile.email == "channel-review@example.com")
        )
        account = session.get(ChannelAccount, channel)
        original_owner = account.owner_id
        account.owner_id = owner.id
        return channel, owner.id, original_owner, {"X-Session-ID": str(token)}


def test_account_listing_checks_current_exact_scope(
    app_client,  # noqa: F811
    database_sessions,
    owned_channel,
):
    account_id, user_id, other_id, headers = owned_channel
    with database_sessions() as session, session.begin():
        actor = load_identity(session, other_id)
        configuration, _ = flow_configuration(session, actor=actor, name="Scope")
        other_configuration, _ = flow_configuration(session, actor=actor, name="Other scope")
        member = ConfigurationMember(
            configuration_id=configuration.id, user_id=user_id, permissions=["manage"]
        )
        other_member = ConfigurationMember(
            configuration_id=other_configuration.id, user_id=user_id, permissions=["read"]
        )
        session.add_all([member, other_member])
        account = session.get(ChannelAccount, account_id)
        account.configuration_id = configuration.id
        shared = ChannelAccount(
            owner_id=other_id,
            configuration_id=configuration.id,
            channel_type="test",
            external_account_id="shared",
            installation_id=account.installation_id,
            version_id=account.version_id,
        )
        other_account = ChannelAccount(
            owner_id=other_id,
            configuration_id=other_configuration.id,
            channel_type="test",
            external_account_id="other",
            installation_id=account.installation_id,
            version_id=account.version_id,
        )
        session.add_all([shared, other_account])
        session.flush()
        configuration_id, other_configuration_id = configuration.id, other_configuration.id
        configuration_accounts = {str(account_id), str(shared.id)}
        other_account_id = str(other_account.id)
        member_id, other_member_id = member.id, other_member.id
    url = "/api/v1/channels/accounts"
    assert app_client.get(url, headers=headers).json() == []
    scoped = app_client.get(
        url, headers=headers, params={"configuration_id": str(configuration_id)}
    )
    assert scoped.status_code == 200, scoped.text
    assert {item["id"] for item in scoped.json()} == configuration_accounts
    other_scope = {"configuration_id": str(other_configuration_id)}
    scoped = app_client.get(url, headers=headers, params=other_scope)
    assert [item["id"] for item in scoped.json()] == [other_account_id]
    with database_sessions() as session, session.begin():
        session.delete(session.get(ConfigurationMember, member_id))
        session.delete(session.get(ConfigurationMember, other_member_id))
    assert app_client.get(url, headers=headers).json() == []
    assert (
        app_client.get(
            url, headers=headers, params={"configuration_id": str(configuration_id)}
        ).status_code
        == 404
    )
    assert app_client.get(url, headers=headers, params=other_scope).status_code == 404


def test_interrupted_send_is_reconciled_without_resending(
    app_client,  # noqa: F811
    database_sessions,
    owned_channel,
):
    account_id, _, _, headers = owned_channel
    with database_sessions() as session, session.begin():
        delivery_id = service.reserve_delivery(
            session,
            account_id=account_id,
            idempotency_key="interrupted",
            conversation_key="one",
            payload={},
            cipher=CIPHER,
        ).id
    url = f"/api/v1/channels/deliveries/{delivery_id}/reconcile"
    interrupted = {"status": "needs_attention", "evidence": "Adapter process terminated"}
    assert app_client.post(url, headers=headers, json=interrupted).status_code == 409
    with database_sessions() as session, session.begin():
        service.claim_delivery(session, delivery_id=delivery_id)
    first = app_client.post(url, headers=headers, json=interrupted)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "needs_attention"
    assert app_client.post(url, headers=headers, json=interrupted).status_code == 200
    with database_sessions() as session, pytest.raises(ConflictError, match="not pending"):
        service.claim_delivery(session, delivery_id=delivery_id)
    confirmed = {"status": "sent", "external_message_id": "vendor-one", "evidence": "Vendor log"}
    assert app_client.post(url, headers=headers, json=confirmed).status_code == 200
    assert app_client.post(url, headers=headers, json=confirmed).status_code == 200
    assert (
        app_client.post(
            url, headers=headers, json={**confirmed, "external_message_id": "different"}
        ).status_code
        == 409
    )
    with database_sessions() as session:
        delivery = session.get(ChannelDelivery, delivery_id)
        assert delivery.attempts == 1 and delivery.status == "sent"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "channel.delivery_reconciled")
            )
            == 2
        )


def test_preloaded_disabled_account_cannot_reserve_delivery(database_sessions, channel):  # noqa: F811
    with database_sessions() as stale:
        cached = stale.get(ChannelAccount, channel)
        assert cached.status == "enabled"
        with database_sessions() as other, other.begin():
            service.change_connection(
                other,
                account_id=channel,
                expected_epoch=1,
                enabled=False,
            )
        with pytest.raises(NotFoundError):
            service.reserve_delivery(
                stale,
                account_id=channel,
                idempotency_key=str(uuid4()),
                conversation_key="one",
                payload={},
                cipher=CIPHER,
            )
