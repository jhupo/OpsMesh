"""Native Temporal Schedules, with tenant-scoped admission for each occurrence."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import uuid5

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.service import RPCError, RPCStatusCode

from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.orchestration.automations.authorization import require_automation_principal
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.models import Automation
from opsmesh.orchestration.automations.service import AutomationService
from opsmesh.orchestration.scheduling.contracts import ScheduleOccurrence
from opsmesh.orchestration.scheduling.models import (
    WorkspaceScheduledJob,
    WorkspaceScheduledJobEvent,
)
from opsmesh.orchestration.scheduling.specs import build_schedule_spec
from opsmesh.orchestration.scheduling.types import COMPLETED_STATUS, EXECUTE_ACTION
from opsmesh.platform.updates.service import maintenance_enabled
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution


@dataclass(frozen=True)
class ScheduleSnapshot:
    revision: int
    active: bool
    schedule_type: str | None
    schedule_config: dict[str, object]
    created_at: datetime


class ScheduleActivities:
    def __init__(self, services: ActivityDependencies, client: Client) -> None:
        self._services = services
        self._client = client

    @activity.defn(name="opsmesh.schedule.synchronize")
    async def synchronize(self, request: ExecutionRequest) -> None:
        owner_kind = str(request.parameters.get("owner_kind"))
        schedule_id = self._schedule_id(request, owner_kind)
        # Capture a versioned snapshot in a short transaction. No database
        # connection stays checked out while Temporal RPC waits for the server.
        for _ in range(3):
            snapshot = await asyncio.to_thread(self._snapshot, request)
            if snapshot.schedule_type is None:
                try:
                    await self._client.get_schedule_handle(schedule_id).delete(
                        rpc_timeout=timedelta(seconds=10)
                    )
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
                next_due = None
            else:
                tick = ExecutionRequest(
                    workspace_id=request.workspace_id,
                    resource_id=request.resource_id,
                    kind=ExecutionKind.SCHEDULE_TICK,
                    parameters={"owner_kind": owner_kind},
                )
                desired = Schedule(
                    action=ScheduleActionStartWorkflow(
                        "opsmesh.schedule_tick",
                        tick,
                        id=schedule_id,
                        task_queue=self._services.settings.temporal_task_queue,
                    ),
                    spec=build_schedule_spec(snapshot.schedule_type, snapshot.schedule_config),
                    policy=SchedulePolicy(
                        overlap=ScheduleOverlapPolicy.BUFFER_ALL, catchup_window=timedelta(days=1)
                    ),
                    state=ScheduleState(paused=not snapshot.active),
                )
                desired.spec.start_at = snapshot.created_at
                try:
                    handle = await self._client.create_schedule(
                        schedule_id, desired, rpc_timeout=timedelta(seconds=10)
                    )
                except ScheduleAlreadyRunningError:
                    handle = self._client.get_schedule_handle(schedule_id)

                    def replace_schedule(
                        _: ScheduleUpdateInput, desired: Schedule = desired
                    ) -> ScheduleUpdate:
                        return ScheduleUpdate(schedule=desired)

                    await handle.update(
                        replace_schedule,
                        rpc_timeout=timedelta(seconds=10),
                    )
                description = await handle.describe(rpc_timeout=timedelta(seconds=10))
                next_due = next(iter(description.info.next_action_times), None)
            if await asyncio.to_thread(self._record_snapshot, request, snapshot, next_due):
                return
        raise ValueError("Schedule changed repeatedly during synchronization")

    def _snapshot(self, request: ExecutionRequest) -> ScheduleSnapshot:
        with self._services.sessions() as session:
            authorize_execution(session, request, action=WorkspaceAction.WRITE)
            item = self._require_owner(session, request)
            if isinstance(item, Automation):
                config = AutomationConfiguration.model_validate(item.configuration)
                schedule_type = (
                    str(config.schedule_type) if config.trigger_type == "schedule" else None
                )
                return ScheduleSnapshot(
                    item.version,
                    item.status == "active",
                    schedule_type,
                    config.schedule_config,
                    item.created_at,
                )
            return ScheduleSnapshot(
                item.revision,
                item.status == "active",
                item.schedule_type,
                item.schedule_config,
                item.created_at,
            )

    def _record_snapshot(
        self, request: ExecutionRequest, snapshot: ScheduleSnapshot, next_due: datetime | None
    ) -> bool:
        with self._services.transaction() as session:
            item = self._require_owner(session, request, lock=True)
            revision = item.version if isinstance(item, Automation) else item.revision
            if revision != snapshot.revision:
                return False
            value = next_due if snapshot.active else None
            if isinstance(item, Automation):
                item.next_due_at = value
            else:
                item.next_run_at = value
            return True

    @staticmethod
    def _require_owner(
        session: Session, request: ExecutionRequest, *, lock: bool = False
    ) -> Automation | WorkspaceScheduledJob:
        owner_kind = request.parameters.get("owner_kind")
        if owner_kind == "automation":
            query = select(Automation).where(
                Automation.workspace_id == request.workspace_id,
                Automation.id == request.resource_id,
            )
            automation = session.scalar(query.with_for_update() if lock else query)
            if automation is None:
                raise ValueError("Automation schedule not found")
            return automation
        if owner_kind == "scheduled_job":
            statement = select(WorkspaceScheduledJob).where(
                WorkspaceScheduledJob.workspace_id == request.workspace_id,
                WorkspaceScheduledJob.id == request.resource_id,
            )
            scheduled_job = session.scalar(statement.with_for_update() if lock else statement)
            if scheduled_job is None:
                raise ValueError("Workspace schedule not found")
            return scheduled_job
        raise ValueError("Unknown schedule owner kind")

    @activity.defn(name="opsmesh.schedule.materialize")
    async def materialize(self, occurrence: ScheduleOccurrence) -> bool:
        if not await asyncio.to_thread(self._materialize, occurrence):
            return False
        request = occurrence.request
        schedule_id = self._schedule_id(request, str(request.parameters.get("owner_kind")))
        description = await self._client.get_schedule_handle(schedule_id).describe(
            rpc_timeout=timedelta(seconds=10)
        )
        next_due = next(iter(description.info.next_action_times), None)
        await asyncio.to_thread(self._record_next_due, request, next_due)
        return True

    def _record_next_due(self, request: ExecutionRequest, next_due: datetime | None) -> None:
        with self._services.transaction() as session:
            item = self._require_owner(session, request, lock=True)
            if item.status != "active":
                return
            if isinstance(item, Automation):
                item.next_due_at = next_due
            else:
                item.next_run_at = next_due

    def _materialize(self, occurrence: ScheduleOccurrence) -> bool:
        request = occurrence.request
        owner_kind = request.parameters.get("owner_kind")
        with self._services.transaction() as session:
            if maintenance_enabled(session):
                return False
            if owner_kind == "automation":
                service = AutomationService(session)
                item = service.require(request.workspace_id, request.resource_id)
                if item.status != "active":
                    return True
                require_automation_principal(session, item)
                config = AutomationConfiguration.model_validate(item.configuration)
                if config.trigger_type != "schedule":
                    return True
                service.accept_scheduled_occurrence(item, occurrence)
            elif owner_kind == "scheduled_job":
                item = session.scalar(
                    select(WorkspaceScheduledJob)
                    .where(
                        WorkspaceScheduledJob.workspace_id == request.workspace_id,
                        WorkspaceScheduledJob.id == request.resource_id,
                    )
                    .with_for_update()
                )
                if item is None or item.status != "active":
                    return True
                prior = session.scalar(
                    select(WorkspaceScheduledJobEvent.id).where(
                        WorkspaceScheduledJobEvent.workspace_id == request.workspace_id,
                        WorkspaceScheduledJobEvent.scheduled_job_id == item.id,
                        WorkspaceScheduledJobEvent.occurrence_id == occurrence.occurrence_id,
                    )
                )
                if prior is not None:
                    return True
                execution = None
                if item.action_type == EXECUTE_ACTION:
                    if item.execution_kind is None or item.resource_id is None:
                        raise ValueError("Schedule execution contract is incomplete")
                    execution = ExecutionRequest(
                        execution_id=uuid5(item.id, occurrence.occurrence_id),
                        workspace_id=item.workspace_id,
                        resource_id=item.resource_id,
                        kind=ExecutionKind(item.execution_kind),
                        requested_by_user_id=item.created_by_user_id,
                        parameters=cast(dict[str, JsonValue], item.parameters),
                    )
                    authorize_execution(session, execution, action=WorkspaceAction.WRITE)
                    stage_execution(session, execution)
                session.add(
                    WorkspaceScheduledJobEvent(
                        workspace_id=item.workspace_id,
                        scheduled_job_id=item.id,
                        due_at=occurrence.started_at,
                        occurrence_id=occurrence.occurrence_id,
                        action_type=item.action_type,
                        status="dispatched" if execution else "recorded",
                        execution_id=execution.execution_id if execution else None,
                        metadata_={"revision": item.revision},
                    )
                )
                item.last_run_at = occurrence.started_at
                item.next_run_at = None
                if item.schedule_type == "one_shot":
                    item.status = COMPLETED_STATUS
                    item.completed_at = occurrence.started_at
            else:
                raise ValueError("Unknown schedule occurrence owner")
            return True

    @staticmethod
    def _schedule_id(request: ExecutionRequest, owner_kind: str) -> str:
        return f"opsmesh:{request.workspace_id}:schedule:{owner_kind}:{request.resource_id}"
