"""Real PG Outbox -> Temporal -> authorized subscription, receipt and replay."""

import asyncio
from uuid import uuid4

from sqlalchemy import select
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from opsmesh.bootstrap.source_notifications import AuthoritativeEvents
from opsmesh.orchestration.runs.models import FlowRun
from opsmesh.orchestration.triggers.activities import TriggerActivities
from opsmesh.orchestration.triggers.models import TriggerReceipt
from opsmesh.orchestration.triggers.schemas import (
    SourceEventWork,
    TriggerConfiguration,
    TriggerCreate,
)
from opsmesh.orchestration.triggers.service import create_trigger
from opsmesh.orchestration.triggers.workflow import SourceEventWorkflow
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.approvals import ApprovalTarget, approvals_for
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ForbiddenError
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from opsmesh.temporal.dispatch.outbox import TemporalOutboxPublisher
from tests.orchestration.test_flows_postgres import create_flow
from tests.orchestration.test_flows_postgres import flow_facts as flow_facts


def test_platform_event_delivers_once_and_replays(
    database_sessions, async_database_sessions, flow_facts
):
    runtime, actor, revision = flow_facts
    with database_sessions() as session, session.begin():
        create_trigger(
            session,
            actor=actor,
            request=TriggerCreate(
                revision_id=revision,
                kind="event",
                configuration=TriggerConfiguration(
                    source_kind="platform", event_type="sample.changed"
                ),
            ),
            cipher=runtime.cipher,
        )
        event = AuditRecord(
            "operation",
            "sample.changed",
            AuditActor.from_user(actor),
            "platform",
            target_type="sample",
            target_id="one",
            summary="Changed",
        )
        record_audit(session, event)
    with database_sessions() as session:
        # Locate the actual transactional intent rather than inventing a Workflow argument.
        entry = next(
            row
            for row in session.scalars(select(TemporalOutboxEntry))
            if runtime.cipher.decrypt_payload(row.sealed_payload, key_id=row.encryption_key_id)[
                "execution"
            ]["source_id"]
            == str(event.event_id)
        )
        workflow_id = entry.workflow_id
        reference = SourceEventWork.model_validate(
            runtime.cipher.decrypt_payload(entry.sealed_payload, key_id=entry.encryption_key_id)[
                "execution"
            ]
        )

    async def verify():
        async with await WorkflowEnvironment.start_local(
            data_converter=pydantic_data_converter
        ) as environment:
            queue = f"events-{uuid4()}"
            activities = TriggerActivities(
                runtime,
                AuthoritativeEvents(runtime.cipher, runtime.scopes),
                environment.client,
                queue,
            )
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[SourceEventWorkflow],
                activities=[activities.consume],
            ):
                publisher = TemporalOutboxPublisher(
                    async_database_sessions,
                    environment.client,
                    task_queue=queue,
                    cipher=runtime.cipher,
                )
                assert await publisher.publish_pending() >= 1
                handle = environment.client.get_workflow_handle(workflow_id)
                assert await asyncio.wait_for(handle.result(), 20) == {"accepted": 1}
                assert activities.consume_source(reference)["accepted"] == 0
                history = await handle.fetch_history()
                with database_sessions() as session:
                    receipt = session.scalars(
                        select(TriggerReceipt).where(
                            TriggerReceipt.event_id == str(reference.execution_id)
                        )
                    ).one()
                    assert receipt.status == "accepted"
                    assert session.get(FlowRun, receipt.flow_run_id).principal_id == actor.user_id
            await Replayer(
                workflows=[SourceEventWorkflow], data_converter=pydantic_data_converter
            ).replay_workflow(history)

    asyncio.run(verify())


def test_platform_approval_source_preserves_flow_parent_and_private_actor(
    database_sessions, flow_facts
):
    runtime, actor, _ = flow_facts
    parent_id = create_flow(database_sessions, flow_facts)
    with database_sessions() as session, session.begin():
        request = approvals_for(session).create(
            session,
            kind="flow_plan",
            target=ApprovalTarget("flow_plan", str(parent_id), "one"),
            title="Review plan",
            summary="",
            details={},
            approver_user_id=actor.user_id,
            requester=actor,
        )
        session.flush()
        occurrence = session.scalars(
            select(AuditEvent).where(AuditEvent.target_id == str(request.id))
        ).one()
        reference = SourceEventWork(
            execution_id=uuid4(),
            workflow_id="source-check",
            source_kind="platform",
            source_id=occurrence.id,
        )
        sources = AuthoritativeEvents(runtime.cipher, runtime.scopes)
        assert sources.read(session, actor=actor, reference=reference).parent_flow_id == parent_id
        from dataclasses import replace

        import pytest

        with pytest.raises(ForbiddenError):
            sources.read(session, actor=replace(actor, user_id=uuid4()), reference=reference)


def test_audit_event_with_opaque_reference_has_no_flow_parent(database_sessions, flow_facts):
    runtime, actor, _ = flow_facts
    with database_sessions() as session, session.begin():
        record = AuditRecord(
            "operation",
            "sample.changed",
            AuditActor.from_user(actor),
            "platform",
            target_type="sample",
            target_id="external:one",
            summary="Changed",
        )
        record_audit(session, record)
        session.flush()
        occurrence = session.scalars(
            select(AuditEvent).where(AuditEvent.id == record.event_id)
        ).one()
        sources = AuthoritativeEvents(runtime.cipher, runtime.scopes)
        assert sources.platform_parent(session, occurrence) is None
