"""Lease-fenced trigger materialization and independent Run reconciliation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

from opsmesh.framework.errors import ErrorInfo, OpsMeshError
from opsmesh.framework.events import DispatchContext, OpsMeshEvent, TimerEvent
from opsmesh.infrastructure.database import StoredRecord
from opsmesh.modules.runtime.api import TERMINAL_STATES
from opsmesh.modules.scheduling.api import (
    OccurrenceStatus,
    PersistentSchedule,
    ScheduleDispatcher,
    ScheduleOccurrence,
    ScheduleOccurrenceObserver,
    ScheduleRunResolver,
    ScheduleSubmissionResolver,
    ScheduleTargetValidator,
)

from .calendar import due_times
from .repository import ACTIVE_OCCURRENCES, ScheduleRepository, occurrence_key


def safe_error(error: Exception) -> ErrorInfo:
    if isinstance(error, OpsMeshError):
        return ErrorInfo(code=error.code, safe_message=error.info.safe_message, phase="schedule")
    return ErrorInfo(
        code="SCHEDULE_FAILED", safe_message="Scheduled event dispatch failed", phase="schedule"
    )


def new_occurrence(
    schedule: PersistentSchedule, scheduled_at: datetime, *, manual_key: str | None = None
) -> ScheduleOccurrence:
    event_id = (
        f"schedule:{schedule.id}:manual:{manual_key}"
        if manual_key
        else f"schedule:{schedule.id}:{schedule.revision}:{scheduled_at.isoformat()}"
    )
    now = datetime.now(UTC)
    return ScheduleOccurrence(
        id=occurrence_key(event_id),
        schedule_id=schedule.id,
        schedule_revision=schedule.revision,
        event_id=event_id,
        scheduled_at=scheduled_at,
        status="queued",
        trigger="manual" if manual_key else "scheduled",
        created_at=now,
        updated_at=now,
    )


class ScheduleExecutionEngine:
    def __init__(
        self,
        repository: ScheduleRepository,
        dispatcher: ScheduleDispatcher,
        validate: ScheduleTargetValidator,
        run_resolver: ScheduleRunResolver | None,
        observer: ScheduleOccurrenceObserver | None,
        submission_resolver: ScheduleSubmissionResolver | None = None,
    ) -> None:
        self.repository, self.dispatcher, self.validate = repository, dispatcher, validate
        self.run_resolver, self.observer = run_resolver, observer
        self.submission_resolver = submission_resolver
        self.errors: list[tuple[str, Exception]] = []

    def record_error(self, schedule_id: str, error: Exception) -> None:
        self.errors.append((schedule_id, error))
        del self.errors[:-100]

    async def reconcile(self) -> int:
        changed = 0
        for record in await self.repository.occurrences():
            occurrence = ScheduleOccurrence.model_validate(record.data["occurrence"])
            configuration = PersistentSchedule.model_validate(record.data["configuration"])
            if (
                self.run_resolver is None
                or occurrence.run_id is None
                or occurrence.status not in ACTIVE_OCCURRENCES
            ):
                await self.notify(occurrence, configuration)
                continue
            try:
                run = await self.run_resolver(occurrence.run_id)
                status: OccurrenceStatus = (
                    "submitted"
                    if run.status == "queued"
                    else "running"
                    if run.status in {"preparing", "finalizing", "resuming"}
                    else cast(OccurrenceStatus, run.status)
                )
                if status == occurrence.status:
                    await self.notify(occurrence, configuration)
                    continue
                now = datetime.now(UTC)
                updates: dict[str, Any] = {"status": status, "updated_at": now}
                if status == "running" and occurrence.started_at is None:
                    updates["started_at"] = run.updated_at
                if status in TERMINAL_STATES:
                    updates["finished_at"] = run.updated_at
                if run.error is not None:
                    updates["error"] = ErrorInfo(
                        code=run.error.code, safe_message=run.error.safe_message, phase="run"
                    )
                updated = occurrence.model_copy(update=updates)
                await self.repository.save_occurrence(record, updated)
                changed += 1
                await self.notify(
                    updated, PersistentSchedule.model_validate(record.data["configuration"])
                )
            except OpsMeshError as exc:
                if exc.code != "CONFLICT":
                    self.record_error(occurrence.schedule_id, exc)
        return changed

    async def notify(self, occurrence: ScheduleOccurrence, schedule: PersistentSchedule) -> None:
        selected = (occurrence.status in {"failed", "blocked"} and schedule.notify_on_failure) or (
            occurrence.status == "waiting_approval" and schedule.notify_on_approval
        )
        if selected and self.observer is not None:
            try:
                record = await self.repository.store.get(occurrence.id)
                if record is None:
                    return
                key = f"{occurrence.status}:{occurrence.updated_at.isoformat()}"
                delivered = list(record.data.get("notifications", []))
                if key in delivered:
                    return
                await self.observer(occurrence, schedule)
                await self.repository.store.update(
                    record.id, {**record.data, "notifications": [*delivered, key]}, record.revision
                )
            except Exception as exc:
                # The persisted occurrence retries notification on the next reconciliation.
                self.record_error(occurrence.schedule_id, exc)

    async def materialize(self, record: StoredRecord, owner: str, horizon: datetime) -> None:
        schedule = PersistentSchedule.model_validate(record.data["schedule"])
        if not schedule.enabled:
            return
        times, following = due_times(schedule, horizon)
        if not times:
            return
        records = await self.repository.occurrences(schedule.id)
        recorded_ids = {item.id for item in records}
        active = sum(r.data["occurrence"]["status"] in ACTIVE_OCCURRENCES for r in records)
        queued = sum(r.data["occurrence"]["status"] == "queued" for r in records)
        actor_id = str(record.data.get("actor_id", schedule.principal_id))
        for instant in times:
            await self.repository.renew(schedule.id, owner)
            occurrence = new_occurrence(schedule, instant)
            if occurrence.id in recorded_ids:
                continue  # A previous owner committed this occurrence before advancing the plan.
            reason = None
            if (
                schedule.misfire_policy == "skip"
                and (horizon - instant).total_seconds() > schedule.misfire_grace_seconds
            ):
                reason = "misfire"
            elif (
                schedule.concurrency_policy == "skip"
                and active + queued >= schedule.max_active_runs
            ):
                reason = "concurrency_limit"
            elif queued >= schedule.max_queued_occurrences:
                reason = "queue_full"
            if reason:
                occurrence = occurrence.model_copy(
                    update={"status": "skipped", "reason": reason, "finished_at": datetime.now(UTC)}
                )
            saved = await self.repository.add_occurrence(occurrence, schedule, actor_id)
            recorded_ids.add(saved.id)
            # Count persisted state, including a record won by an overlapping writer.
            queued += int(saved.data["occurrence"]["status"] == "queued")
            active += int(saved.data["occurrence"]["status"] in ACTIVE_OCCURRENCES)
        updates: dict[str, Any] = {"enabled": following is not None}
        if following is not None:
            updates["next_run_at"] = following
        await self.repository.patch_plan(
            schedule.id,
            owner,
            {
                "kind": "schedule",
                "schedule": schedule.model_copy(update=updates).model_dump(mode="json"),
                "state": "ready" if following is not None else "exhausted",
            },
        )

    async def dispatch_pending(self, schedule_id: str, owner: str) -> int:
        plan = await self.repository.owned(schedule_id, owner)
        schedule = PersistentSchedule.model_validate(plan.data["schedule"])
        records = await self.repository.occurrences(schedule_id)
        # A dispatching row from an expired owner is replayed with its stable event key.
        pending = [
            r for r in records if r.data["occurrence"]["status"] in {"queued", "dispatching"}
        ]
        active = sum(
            r.data["occurrence"]["status"] in {"submitted", "running", "waiting_approval"}
            for r in records
        )
        dispatched = 0
        for record in pending[: schedule.max_catch_up]:
            occurrence = ScheduleOccurrence.model_validate(record.data["occurrence"])
            if (
                not schedule.enabled
                and plan.data.get("state") != "exhausted"
                and occurrence.trigger != "manual"
            ):
                continue
            if active >= schedule.max_active_runs:
                break
            await self.repository.renew(schedule_id, owner)
            success, submitted = await self._dispatch(record, owner)
            dispatched += int(success)
            active += int(submitted)
            if not success:
                break
        return dispatched

    async def _dispatch(self, record: StoredRecord, owner: str) -> tuple[bool, bool]:
        occurrence = ScheduleOccurrence.model_validate(record.data["occurrence"])
        schedule = PersistentSchedule.model_validate(record.data["configuration"])
        actor_id = str(record.data["actor_id"])
        occurrence = occurrence.model_copy(
            update={"status": "dispatching", "updated_at": datetime.now(UTC)}
        )
        record = await self.repository.save_occurrence(record, occurrence)
        event = TimerEvent(
            id=occurrence.event_id,
            source="persistent_scheduler",
            job_id=schedule.id,
            scheduled_at=occurrence.scheduled_at,
            payload=schedule.payload,
        )
        context: DispatchContext[OpsMeshEvent] = DispatchContext(
            event=event,
            reply_target=schedule.reply_target,
            values={
                "principal_id": schedule.principal_id,
                "agent_revision": schedule.agent_revision,
                "idempotency_key": occurrence.event_id,
                "schedule_id": schedule.id,
                "occurrence_id": occurrence.id,
                "input": schedule.input,
                "run_overrides": schedule.run_overrides.model_dump(mode="json"),
                "session_policy": schedule.session_policy,
                "session_id": schedule.session_id,
                "notify_on_failure": schedule.notify_on_failure,
                "notify_on_approval": schedule.notify_on_approval,
            },
        )
        try:
            async with asyncio.timeout(self.repository.lease_seconds * 0.8):
                await self.validate(schedule, actor_id)
                result = await self.dispatcher(context)
                if result.errors:
                    raise OpsMeshError("SCHEDULE_FAILED", "Scheduled event handler failed")
                run_id = context.values.get("run_id")
                submitted = isinstance(run_id, str) and bool(run_id)
                if result.matched == 0 and not submitted:
                    raise OpsMeshError(
                        "SCHEDULE_UNHANDLED", "No handler accepted the scheduled event"
                    )
                if schedule.agent_revision and not submitted:
                    raise OpsMeshError(
                        "SCHEDULE_RESULT_MISSING",
                        "Agent dispatcher did not report its submitted Run",
                    )
            await self.repository.owned(schedule.id, owner)
        except Exception as exc:
            self.record_error(schedule.id, exc)
            # A stale owner must never acknowledge or overwrite its successor's result.
            await self.repository.owned(schedule.id, owner)
            uncertain = not isinstance(exc, OpsMeshError) or (
                exc.code
                not in {"IDEMPOTENCY_CONFLICT", "CONFLICT", "FORBIDDEN", "VALIDATION_ERROR"}
                and (exc.retryable or exc.code == "SCHEDULE_RESULT_MISSING")
            )
            if schedule.agent_revision and uncertain and self.submission_resolver is not None:
                recovered = await self.submission_resolver(
                    schedule.principal_id, occurrence.event_id
                )
                if recovered is not None:
                    if (
                        recovered.principal_id == schedule.principal_id
                        and recovered.published_agent_ref == schedule.agent_revision
                        and recovered.metadata.get("event_id") == occurrence.event_id
                        and recovered.metadata.get("schedule_id") == schedule.id
                        and recovered.metadata.get("occurrence_id") == occurrence.id
                    ):
                        await self.repository.owned(schedule.id, owner)
                        return await self._acknowledge(record, occurrence, owner, recovered.id)
                    exc = OpsMeshError(
                        "IDEMPOTENCY_CONFLICT",
                        "Existing Run does not match the scheduled occurrence",
                    )
            failed = occurrence.model_copy(
                update={
                    "status": "failed",
                    "error": safe_error(exc),
                    "updated_at": datetime.now(UTC),
                    "finished_at": datetime.now(UTC),
                }
            )
            await self.repository.save_occurrence(record, failed)
            plan = await self.repository.owned(schedule.id, owner)
            current = PersistentSchedule.model_validate(plan.data["schedule"])
            await self.repository.patch_plan(
                schedule.id,
                owner,
                {
                    "schedule": current.model_copy(update={"enabled": False}).model_dump(
                        mode="json"
                    ),
                    "state": "failed",
                    "last_event_id": event.id,
                },
            )
            await self.notify(failed, schedule)
            return False, False
        return await self._acknowledge(record, occurrence, owner, run_id if submitted else None)

    async def _acknowledge(
        self, record: StoredRecord, occurrence: ScheduleOccurrence, owner: str, run_id: str | None
    ) -> tuple[bool, bool]:
        now = datetime.now(UTC)
        submitted = run_id is not None
        completed = occurrence.model_copy(
            update={
                "status": "submitted" if submitted else "completed",
                "run_id": run_id,
                "submitted_at": now,
                "updated_at": now,
                "finished_at": None if submitted else now,
            }
        )
        await self.repository.save_occurrence(record, completed)
        await self.repository.patch_plan(
            occurrence.schedule_id, owner, {"last_event_id": occurrence.event_id}
        )
        return True, submitted
