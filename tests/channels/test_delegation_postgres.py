"""Real owner authorization from channel Inbox to a fixed FlowRun via the host API."""

import asyncio
from uuid import UUID

import pytest
from opsmesh_sdk.manifest import PackageManifest, Permission
from sqlalchemy import func, select

from opsmesh.bootstrap.extensions import ExtensionPublicAPI
from opsmesh.channels.models import (
    ChannelAccount,
    ChannelIdentityMapping,
    ChannelInbox,
    ChannelRoute,
)
from opsmesh.channels.service import reserve_delivery
from opsmesh.extensions.models import ExtensionInstance, Installation, Invocation
from opsmesh.identity.users.service import create_user, identity_of, load_identity
from opsmesh.orchestration.configurations.models import ConfigurationMember
from opsmesh.orchestration.runs.models import FlowRun
from opsmesh.platform.operations.admission import set_drained
from opsmesh.shared.errors import ForbiddenError
from tests.channels.helpers import flow_configuration
from tests.execution.test_session_routes import app_client as app_client
from tests.extensions.test_extensions_postgres import CIPHER
from tests.extensions.test_extensions_postgres import facts as facts


def test_verified_ingress_routes_once_and_revoked_mapping_is_rejected(
    app_client,  # noqa: F811
    database_sessions,
    facts,  # noqa: F811
):
    user_id, instance_id, installation_id, _, invocation_id = facts
    app_client.app.state.secret_cipher = CIPHER
    permission = "channels.accounts.inbox.receive"
    connection_permission = "channels.accounts.connection.read"
    with database_sessions() as session, session.begin():
        actor = load_identity(session, user_id)
        installation = session.get(Installation, installation_id)
        manifest = PackageManifest.model_validate(installation.manifest)
        installation.manifest = manifest.model_copy(
            update={
                "permissions": (
                    *manifest.permissions,
                    Permission(name=permission, reason="Channel ingress"),
                    Permission(name=connection_permission, reason="Channel connection"),
                )
            }
        ).model_dump(mode="json")
        installation.permission_ceiling = [
            *installation.permission_ceiling,
            permission,
            connection_permission,
        ]
        instance = session.get(ExtensionInstance, instance_id)
        instance.permissions = [*instance.permissions, permission, connection_permission]
        invocation = session.get(Invocation, invocation_id)
        invocation.snapshot = {**invocation.snapshot, "permissions": instance.permissions}
        configuration, revision = flow_configuration(session, actor=actor, name="Receive")
        instance.configuration_id = configuration.id
        invocation.snapshot = {**invocation.snapshot, "configuration_id": str(configuration.id)}
        account = ChannelAccount(
            owner_id=user_id,
            configuration_id=configuration.id,
            channel_type="test",
            external_account_id="bot",
            installation_id=installation.id,
            version_id=installation.version_id,
        )
        session.add(account)
        session.flush()
        mapping = ChannelIdentityMapping(
            account_id=account.id, user_id=user_id, external_identity="sender"
        )
        session.add_all(
            [
                mapping,
                ChannelRoute(
                    account_id=account.id,
                    conversation_key="private:one",
                    configuration_id=configuration.id,
                    flow_revision_id=revision.id,
                    input_mapping={"question": "text"},
                ),
            ]
        )
        account_id = account.id
        session.flush()
        mapping_id = mapping.id

    api = ExtensionPublicAPI(
        app_client.app, database_sessions, app_client.app.state.extension_scopes, CIPHER
    )
    connection = asyncio.run(
        api.request(
            invocation_id=invocation_id,
            operation_id="connection:1",
            request={
                "method": "GET",
                "path": f"/api/v1/channels/accounts/{account_id}/connection",
                "query": {"expected_epoch": "1"},
                "timeout_seconds": 10,
            },
        )
    )
    assert connection["status"] == 200, connection
    assert "secret" not in connection["body"]
    forged = app_client.get(
        f"/api/v1/channels/accounts/{account_id}/connection?expected_epoch=1",
        headers={
            "X-OpsMesh-Installation-Id": str(installation_id),
            "X-OpsMesh-Instance-Id": str(instance_id),
            "X-OpsMesh-Invocation-Id": str(invocation_id),
            "X-OpsMesh-Namespace": "example",
        },
    )
    assert forged.status_code == 401

    async def receive(message_id):
        return await api.request(
            invocation_id=invocation_id,
            operation_id=message_id,
            request={
                "method": "POST",
                "timeout_seconds": 10,
                "path": f"/api/v1/channels/accounts/{account_id}/inbox",
                "body": {
                    "epoch": 1,
                    "external_message_id": message_id,
                    "external_identity": "sender",
                    "conversation_key": "private:one",
                    "payload": {"text": "hello"},
                },
            },
        )

    first = asyncio.run(receive("one"))
    assert first["status"] == 202, first
    assert first["body"]["status"] == "routed"
    replay = asyncio.run(receive("one"))
    assert replay["body"]["inbox_id"] == first["body"]["inbox_id"]
    assert not replay["body"]["accepted"]
    with database_sessions() as session, session.begin():
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 1
        inbox = session.get(ChannelInbox, UUID(first["body"]["inbox_id"]))
        assert inbox.flow_run_id is not None
        session.get(ChannelIdentityMapping, mapping_id).status = "revoked"
    rejected = asyncio.run(receive("two"))
    assert rejected["body"]["status"] == "rejected"
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(FlowRun)) == 1


@pytest.mark.parametrize(
    "mode,expected", [("drained", 409), ("missing", 503), ("viewer", 403), ("open", 200)]
)
def test_delivery_claim_requires_admission_and_write_scope(
    app_client,  # noqa: F811
    database_sessions,
    facts,  # noqa: F811
    mode,
    expected,
):
    user_id, instance_id, installation_id, _, invocation_id = facts
    app_client.app.state.secret_cipher = CIPHER
    permission = "channels.deliveries.claim"
    with database_sessions() as session, session.begin():
        installation = session.get(Installation, installation_id)
        manifest = PackageManifest.model_validate(installation.manifest)
        installation.manifest = manifest.model_copy(
            update={
                "permissions": (*manifest.permissions, Permission(name=permission, reason="Send")),
            }
        ).model_dump(mode="json")
        installation.permission_ceiling = [*installation.permission_ceiling, permission]
        instance = session.get(ExtensionInstance, instance_id)
        instance.permissions = [*instance.permissions, permission]
        invocation = session.get(Invocation, invocation_id)
        invocation.snapshot = {**invocation.snapshot, "permissions": instance.permissions}
        configuration_id = None
        if mode == "viewer":
            owner = create_user(session, email="other-channel@example.com", display_name="Owner")
            configuration, _ = flow_configuration(session, actor=identity_of(owner), name="View")
            configuration_id = configuration.id
            session.add(
                ConfigurationMember(
                    configuration_id=configuration.id, user_id=user_id, permissions=["read"]
                )
            )
        account = ChannelAccount(
            owner_id=user_id,
            configuration_id=configuration_id,
            channel_type="test",
            external_account_id="outbound",
            installation_id=installation_id,
            version_id=installation.version_id,
        )
        session.add(account)
        session.flush()
        delivery_id = reserve_delivery(
            session,
            account_id=account.id,
            idempotency_key="outbound",
            conversation_key="one",
            payload={},
            cipher=CIPHER,
        ).id
        if mode == "drained":
            set_drained(session, drained=True)
    if mode == "missing":
        app_client.app.state.channel_admission = None
    api = ExtensionPublicAPI(
        app_client.app, database_sessions, app_client.app.state.extension_scopes, CIPHER
    )
    call = api.request(
        invocation_id=invocation_id,
        operation_id=f"claim:{mode}",
        request={
            "method": "POST",
            "path": f"/api/v1/channels/deliveries/{delivery_id}/claim",
            "timeout_seconds": 10,
        },
    )
    if mode == "viewer":
        with pytest.raises(ForbiddenError, match="outside the Invocation scope"):
            asyncio.run(call)
    else:
        result = asyncio.run(call)
        assert result["status"] == expected, result
    from opsmesh.channels.models import ChannelDelivery

    with database_sessions() as session:
        delivery = session.get(ChannelDelivery, delivery_id)
        assert delivery.attempts == (1 if mode == "open" else 0)
        assert delivery.status == ("sending" if mode == "open" else "pending")
