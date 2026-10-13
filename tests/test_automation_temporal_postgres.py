"""Entry identity, duplicate admission and reply preparation use durable records."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from opsmesh_plugin_sdk.messaging.contracts import IncomingMessage
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.messaging.channels.models import ChannelDelivery
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.models import (
    Automation,
    AutomationEvent,
    ExternalIdentityBinding,
)
from opsmesh.orchestration.automations.service import AutomationService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.db.base import Base
from tests.fixtures.channels import seed_channel
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def _config(**values):
    return AutomationConfiguration(
        name="Message entry",
        orchestration_definition_id=uuid4(),
        orchestration_version=1,
        agent_team_id=uuid4(),
        trigger_type="message",
        allowed_senders=["channel-user"],
        **values,
    )


def test_received_message_stages_one_entry_and_rechecks_revoked_identity() -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            user, workspace = seed_workspace(session)
            item = Automation(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                configuration=_config().model_dump(mode="json"),
                execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
            )
            session.add(item)
            session.flush()
            binding = ExternalIdentityBinding(
                workspace_id=workspace.id,
                automation_id=item.id,
                sender_id="channel-user",
                user_id=user.id,
                status="active",
            )
            session.add(binding)
            session.commit()
            service = AutomationService(session)
            message = IncomingMessage(
                event_id="message-1",
                conversation_id="chat-1",
                sender_id="channel-user",
                occurred_at=datetime.now(UTC),
                text="Hello",
            )
            event = service.receive(workspace.id, item.id, user.id, message)
            duplicate = service.receive(workspace.id, item.id, user.id, message)
            assert duplicate.id == event.id
            starts = list(session.scalars(select(TemporalOutboxEntry)))
            assert len(starts) == 1
            assert starts[0].payload["execution"]["kind"] == "entry_event"
            binding.status = "revoked"
            session.commit()
            assert service.dispatch_event(workspace.id, event.id) == "rejected"
            session.commit()
            assert event.task_id is None
            assert event.error_code == "automation_admission_rejected"


def test_reply_preparation_reuses_delivery_and_commits_completion_without_scan() -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            user, workspace = seed_workspace(session)
            connection, _, _ = seed_channel(session, user, workspace)
            config = _config(reply_connection_id=connection.id, channel_connection_id=connection.id)
            identity = ExecutionIdentityService(session).capture(workspace.id, user.id)
            item = Automation(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                configuration=config.model_dump(mode="json"),
                execution_identity=identity,
            )
            task = Task(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                title="Reply",
                status="completed",
                final_output={"result": "hello"},
            )
            session.add_all([item, task])
            session.flush()
            session.add(
                ExternalIdentityBinding(
                    workspace_id=workspace.id,
                    automation_id=item.id,
                    sender_id="channel-user",
                    user_id=user.id,
                    status="active",
                )
            )
            event = AutomationEvent(
                workspace_id=workspace.id,
                automation_id=item.id,
                external_event_id="reply-1",
                conversation_id="chat",
                content_hash="hash",
                configuration=config.model_dump(mode="json"),
                input_payload={"sender_id": "channel-user"},
                execution_identity=identity,
                status="dispatched",
                task_id=task.id,
            )
            session.add(event)
            session.commit()
            service = AutomationService(session)
            first = service.prepare_reply(workspace.id, event.id)
            second = service.prepare_reply(workspace.id, event.id)
            assert first.delivery_id == second.delivery_id
            assert not first.complete
            session.commit()
            assert event.reply_final
            attempt = session.get(ChannelDelivery, first.delivery_id)
            assert attempt.payload["output"] == {"result": "hello"}
            assert not list(session.scalars(select(TemporalOutboxEntry)))
            attempt.status = "delivered"
            assert service.settle_reply(workspace.id, event.id)
            session.commit()
            assert event.status == "completed"
