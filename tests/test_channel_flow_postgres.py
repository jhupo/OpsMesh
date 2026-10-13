"""A durable channel event routes once; replies wait for external evidence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from opsmesh_plugin_sdk.messaging.contracts import (
    ChannelEvent,
    IncomingMessage,
    ReplyReceiptRequest,
)
from sqlalchemy import func, select

from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.messaging.channels.delivery import record_receipt
from opsmesh.messaging.channels.models import ChannelDelivery, ChannelIngress
from opsmesh.messaging.channels.service import ChannelService
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.models import Automation, ExternalIdentityBinding
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from tests.fixtures.channels import seed_channel
from tests.fixtures.database import product_session
from tests.fixtures.temporal import seed_workspace


def test_channel_duplicate_routing_receipt_and_live_revocation():
    session = product_session()
    owner, workspace = seed_workspace(session)
    connection, principal, _ = seed_channel(session, owner, workspace)
    config = AutomationConfiguration(
        name="Channel route",
        trigger_type="message",
        orchestration_definition_id=uuid4(),
        orchestration_version=1,
        agent_team_id=uuid4(),
        channel_connection_id=connection.id,
        reply_connection_id=connection.id,
        allowed_senders=["staff"],
        allowed_conversations=["private"],
    )
    automation = Automation(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        configuration=config.model_dump(mode="json"),
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, owner.id),
    )
    session.add(automation)
    session.flush()
    session.add(
        ExternalIdentityBinding(
            workspace_id=workspace.id,
            automation_id=automation.id,
            sender_id="staff",
            user_id=owner.id,
        )
    )
    session.commit()
    envelope = ChannelEvent(
        id="external-event",
        source=f"opsmesh://channels/{connection.id}",
        data=IncomingMessage(
            event_id="external-event",
            conversation_id="private",
            sender_id="staff",
            text="hello",
            occurred_at=datetime.now(UTC),
        ),
    )
    service = ChannelService(session)
    event = service.receive(principal, connection.id, envelope)
    session.commit()
    config.name = "Renamed"
    config.allowed_conversations = ["changed"]
    automation.configuration = config.model_dump(mode="json")
    session.commit()
    assert service.receive(principal, connection.id, envelope).id == event.id
    assert session.scalar(select(func.count()).select_from(ChannelIngress)) == 1
    assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 1
    with pytest.raises(ResourceAccessDeniedError):
        service.receive(
            principal,
            connection.id,
            envelope.model_copy(update={"source": "opsmesh://channels/forged"}),
        )
    session.rollback()
    delivery = ChannelDelivery(
        workspace_id=workspace.id,
        connection_id=connection.id,
        event_id=event.id,
        sequence=1,
        connection_revision=connection.configuration_revision,
        payload={},
        status="sending",
        accepted_at=datetime.now(UTC),
    )
    session.add(delivery)
    session.commit()
    assert delivery.status == "sending"
    receipt = ReplyReceiptRequest(
        delivery_id=delivery.id, status="delivered", provider_message_id="channel-123"
    )
    record_receipt(session, delivery, receipt)
    session.commit()
    record_receipt(session, delivery, receipt)
    session.commit()
    assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 2
    connection.status = "disabled"
    session.commit()
    with pytest.raises(ResourceAccessDeniedError):
        service.receive(principal, connection.id, envelope)
