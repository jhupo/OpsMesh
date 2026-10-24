"""Schedule reconciliation and authorized trigger Activities, outside Workflow code."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.exceptions import ApplicationError

from opsmesh.platform.targets import TargetPorts
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.service import current_user
from opsmesh.workflows.triggers import service
from opsmesh.workflows.triggers.models import Trigger, TriggerDelivery

logger = logging.getLogger(__name__)


class TriggerActivities:
    def __init__(
        self, sessions: sessionmaker[Session], cipher: SecretCipher, ports: TargetPorts
    ) -> None:
        self.sessions, self.cipher, self.ports = sessions, cipher, ports

    @activity.defn(name="trigger.fire")
    async def fire(self, command: dict[str, object]) -> dict[str, object]:
        def apply() -> dict[str, object]:
            with self.sessions.begin() as session:
                row = session.get(Trigger, UUID(str(command["trigger_id"])), with_for_update=True)
                if row is None:
                    return {"skipped": True}
                previous = session.scalar(
                    select(TriggerDelivery).where(
                        TriggerDelivery.trigger_id == row.id,
                        TriggerDelivery.event_id == str(command["event_id"]),
                    )
                )
                if previous is not None:
                    return {"trigger_id": str(row.id), "delivery_id": str(previous.id)}
                spec = service.specification(row, self.cipher)
                if (
                    row.status != "active"
                    or not spec.enabled
                    or row.revision != int(str(command["revision"]))
                ):
                    return {"skipped": True}
                receipt = service.fire(
                    session,
                    row=row,
                    event_id=str(command["event_id"]),
                    cipher=self.cipher,
                    ports=self.ports,
                    expected_revision=row.revision,
                )
                return {"trigger_id": str(row.id), "delivery_id": str(receipt.id)}

        try:
            return await asyncio.to_thread(apply)
        except DomainError as exc:
            raise ApplicationError(exc.code, non_retryable=True) from exc

    def _delivery(
        self, session: Session, payload: dict[str, object]
    ) -> tuple[Trigger, TriggerDelivery]:
        row = session.get(Trigger, UUID(str(payload["trigger_id"])))
        delivery = session.get(TriggerDelivery, UUID(str(payload["delivery_id"])))
        if row is None or delivery is None or delivery.trigger_id != row.id:
            raise ApplicationError("Trigger delivery is unavailable", non_retryable=True)
        return row, delivery

    @activity.defn(name="trigger.result")
    async def result(self, payload: dict[str, object]) -> dict[str, object]:
        def read() -> dict[str, object]:
            with self.sessions.begin() as session:
                row, delivery = self._delivery(session, payload)
                result = self.ports.result(
                    session,
                    current_user(session, row.owner_user_id),
                    delivery.target_kind,
                    delivery.run_id,
                )
                return {"status": result["status"], "run_id": str(delivery.run_id)}

        return await asyncio.to_thread(read)

    @activity.defn(name="trigger.cancel")
    async def cancel(self, payload: dict[str, object]) -> None:
        def apply() -> None:
            with self.sessions.begin() as session:
                row, delivery = self._delivery(session, payload)
                self.ports.cancel(
                    session,
                    current_user(session, row.owner_user_id),
                    delivery.target_kind,
                    delivery.run_id,
                )

        await asyncio.to_thread(apply)


class ScheduleReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        cipher: SecretCipher,
        client: Client,
        task_queue: str,
    ) -> None:
        self.sessions, self.cipher, self.client, self.task_queue = (
            sessions,
            cipher,
            client,
            task_queue,
        )

    async def reconcile(self, identifier: UUID) -> None:
        # A transaction advisory lock serializes the external write across Worker processes.
        # The DB connection is held only for bounded Schedule RPCs, never between polling cycles.
        async with self.sessions.begin() as session:
            acquired = await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"schedule:{identifier}"},
            )
            if not acquired:
                return
            row = await session.get(Trigger, identifier, with_for_update=True)
            if row is None or row.applied_revision == row.revision:
                return
            if row.kind != "schedule":
                row.applied_revision = row.revision
                return
            spec = service.specification(row, self.cipher)
            schedule = Schedule(
                action=ScheduleActionStartWorkflow(
                    "TriggerWorkflow",
                    {"trigger_id": str(row.id), "revision": row.revision},
                    id=f"trigger:{row.id}",
                    task_queue=self.task_queue,
                ),
                spec=ScheduleSpec(
                    cron_expressions=[spec.cron] if spec.cron else [], time_zone_name=spec.timezone
                ),
                policy=SchedulePolicy(
                    overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(minutes=5)
                ),
                state=ScheduleState(paused=not spec.enabled or row.status == "deleted"),
            )
            try:
                try:
                    await self.client.create_schedule(
                        f"trigger:{row.id}", schedule, rpc_timeout=timedelta(seconds=10)
                    )
                except ScheduleAlreadyRunningError:
                    await self.client.get_schedule_handle(f"trigger:{row.id}").update(
                        lambda _: ScheduleUpdate(schedule), rpc_timeout=timedelta(seconds=10)
                    )
            except Exception as exc:
                logger.warning(
                    "Schedule reconciliation failed",
                    extra={"trigger_id": str(row.id), "error_type": type(exc).__name__},
                )
                row.sync_error = type(exc).__name__[:100]
                row.updated_at = datetime.now(UTC)
            else:
                row.applied_revision, row.sync_error = row.revision, None

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            async with self.sessions() as session:
                identifiers = list(
                    await session.scalars(
                        select(Trigger.id)
                        .where(Trigger.applied_revision != Trigger.revision)
                        .order_by(Trigger.updated_at, Trigger.id)
                        .limit(100)
                    )
                )
            for identifier in identifiers:
                if stop.is_set():
                    return
                await self.reconcile(identifier)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=5)
