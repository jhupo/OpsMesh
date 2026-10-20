"""Authorized event consumption and the official Temporal Schedule API."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, true
from sqlalchemy.dialects.postgresql import insert
from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)

from opsmesh.orchestration.definitions.service import load_revision
from opsmesh.orchestration.runs.activities import FlowActivities
from opsmesh.orchestration.runs.models import FlowRun
from opsmesh.orchestration.triggers.contracts import SourceEvents
from opsmesh.orchestration.triggers.models import EventSubscription, FlowTrigger, TriggerReceipt
from opsmesh.orchestration.triggers.schemas import (
    SourceEventWork,
    TriggerConfiguration,
    TriggerWork,
)
from opsmesh.orchestration.triggers.service import deliver, lock_delivery
from opsmesh.shared.errors import ConflictError, DomainError
from opsmesh.shared.serialization import payload_digest


class TriggerActivities:
    def __init__(
        self, runtime: FlowActivities, sources: SourceEvents, client: Client, task_queue: str
    ) -> None:
        self.runtime, self.sources, self.client, self.task_queue = (
            runtime,
            sources,
            client,
            task_queue,
        )

    @activity.defn(name="triggers.consume")
    async def consume(self, reference: SourceEventWork) -> dict[str, Any]:
        return await asyncio.to_thread(self.consume_source, reference)

    def consume_source(self, reference: SourceEventWork) -> dict[str, Any]:
        count = 0
        with self.runtime.sessions() as session:
            ids = list(
                session.scalars(
                    select(EventSubscription.id)
                    .where(
                        EventSubscription.source_kind == reference.source_kind,
                        EventSubscription.id > reference.after_subscription
                        if reference.after_subscription
                        else true(),
                    )
                    .order_by(EventSubscription.id)
                    .limit(64)
                )
            )
        for identifier in ids:
            with self.runtime.sessions() as session, session.begin():
                subscription = session.get(EventSubscription, identifier)
                if subscription is None:
                    continue
                trigger = session.get(FlowTrigger, subscription.trigger_id)
                if (
                    trigger is None
                    or trigger.status != "enabled"
                    or trigger.revision != subscription.consumer_revision
                ):
                    continue
                actor = self.runtime.users.load_identity(session, trigger.principal_id)
                if actor is None or actor.status != "active":
                    continue
                event_id = str(reference.execution_id)
                if session.scalar(
                    select(TriggerReceipt.id).where(
                        TriggerReceipt.trigger_id == trigger.id,
                        TriggerReceipt.consumer_revision == trigger.revision,
                        TriggerReceipt.event_id == event_id,
                    )
                ):
                    continue
                try:
                    with session.begin_nested():
                        source = self.sources.read(session, actor=actor, reference=reference)
                        config = TriggerConfiguration.model_validate(trigger.configuration)
                        if (
                            source.event_type != subscription.event_type
                            or config.instance_id != source.instance_id
                        ):
                            continue
                        _, target = load_revision(
                            session, actor=actor, revision_id=trigger.revision_id, use=True
                        )
                        if source.configuration_id != trigger.configuration_id:
                            raise ConflictError(
                                "The trigger source is outside its fixed target scope"
                            )
                        parent = (
                            session.get(FlowRun, source.parent_flow_id)
                            if source.parent_flow_id
                            else None
                        )
                        trigger = lock_delivery(
                            session, runtime=self.runtime, trigger_id=trigger.id, parent=parent
                        )
                        if trigger.revision != subscription.consumer_revision:
                            continue
                        deliver(
                            session,
                            runtime=self.runtime,
                            trigger=trigger,
                            event_id=event_id,
                            payload=source.payload,
                            parent=parent,
                        )
                        count += 1
                except DomainError:
                    session.execute(
                        insert(TriggerReceipt)
                        .values(
                            trigger_id=trigger.id,
                            consumer_revision=trigger.revision,
                            event_id=event_id,
                            fingerprint=payload_digest({"source": event_id}),
                            status="rejected",
                            flow_run_id=None,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["trigger_id", "consumer_revision", "event_id"]
                        )
                    )
        return {"accepted": count, "after_subscription": str(ids[-1]) if len(ids) == 64 else None}

    def schedule_configuration(self, identifier: UUID) -> tuple[FlowTrigger, TriggerConfiguration]:
        with self.runtime.sessions() as session:
            record = session.get(FlowTrigger, identifier)
            if record is None or record.kind != "calendar":
                raise ConflictError("The calendar trigger disappeared")
            return record, TriggerConfiguration.model_validate(record.configuration)

    @activity.defn(name="triggers.sync")
    async def sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        record, config = await asyncio.to_thread(
            self.schedule_configuration, UUID(payload["trigger_id"])
        )
        identifier = uuid5(NAMESPACE_URL, f"calendar:{record.id}:{record.revision}")
        work = TriggerWork(
            execution_id=identifier,
            workflow_id=f"calendar:{record.id}:{record.revision}",
            trigger_id=record.id,
            revision=record.revision,
            action="fire",
        )
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                "TriggerWorkflow", work, id=work.workflow_id, task_queue=self.task_queue
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(seconds=config.interval_seconds))]
                if config.interval_seconds
                else [],
                cron_expressions=[config.cron] if config.cron else [],
                time_zone_name=config.time_zone,
            ),
            state=ScheduleState(
                paused=record.status != "enabled", note=f"opsmesh-revision:{record.revision}"
            ),
        )
        schedule_id = f"opsmesh-trigger:{record.id}"
        try:
            await self.client.create_schedule(schedule_id, schedule)
        except ScheduleAlreadyRunningError:

            def update(current: Any) -> ScheduleUpdate | None:
                note = current.description.schedule.state.note or ""
                if note.startswith("opsmesh-revision:"):
                    revision = int(note.removeprefix("opsmesh-revision:"))
                    if revision > record.revision:
                        return None
                return ScheduleUpdate(schedule=schedule)

            await self.client.get_schedule_handle(schedule_id).update(update)
        return {"status": "synchronized", "revision": record.revision}

    @activity.defn(name="triggers.fire")
    async def fire(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.fire_calendar, payload)

    def fire_calendar(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.runtime.sessions() as session, session.begin():
            trigger = session.get(FlowTrigger, UUID(payload["trigger_id"]))
            if (
                trigger is None
                or trigger.kind != "calendar"
                or trigger.revision != payload["revision"]
                or trigger.status != "enabled"
            ):
                return {"status": "revoked"}
            event_id = f"calendar:{payload['occurrence']}"
            fingerprint = payload_digest({"occurrence": payload["occurrence"]})
            receipt = session.scalar(
                select(TriggerReceipt).where(
                    TriggerReceipt.trigger_id == trigger.id,
                    TriggerReceipt.consumer_revision == trigger.revision,
                    TriggerReceipt.event_id == event_id,
                )
            )
            if receipt is None:
                try:
                    with session.begin_nested():
                        trigger = lock_delivery(
                            session, runtime=self.runtime, trigger_id=trigger.id
                        )
                        if trigger.revision != payload["revision"] or trigger.status != "enabled":
                            return {"status": "revoked"}
                        receipt = deliver(
                            session,
                            runtime=self.runtime,
                            trigger=trigger,
                            event_id=event_id,
                            payload={"occurrence": payload["occurrence"]},
                        )
                except DomainError:
                    session.execute(
                        insert(TriggerReceipt)
                        .values(
                            trigger_id=trigger.id,
                            consumer_revision=payload["revision"],
                            event_id=event_id,
                            fingerprint=fingerprint,
                            status="rejected",
                        )
                        .on_conflict_do_nothing(
                            index_elements=["trigger_id", "consumer_revision", "event_id"]
                        )
                    )
                    receipt = session.scalar(
                        select(TriggerReceipt).where(
                            TriggerReceipt.trigger_id == trigger.id,
                            TriggerReceipt.consumer_revision == payload["revision"],
                            TriggerReceipt.event_id == event_id,
                        )
                    )
            assert receipt is not None
            if receipt.fingerprint != fingerprint:
                raise ConflictError("The calendar occurrence identity changed")
            return {"status": receipt.status, "flow_run_id": str(receipt.flow_run_id)}
