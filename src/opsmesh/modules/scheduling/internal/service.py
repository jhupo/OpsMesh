"""Authorized durable scheduling, independent from HTTP or business plugins."""

from __future__ import annotations

import asyncio
import hashlib
import math
from datetime import UTC, datetime

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.modules.scheduling.api import (
    PersistentSchedule,
    ScheduleAgentResolver,
    ScheduleAuthorizer,
    ScheduleDispatcher,
    ScheduleOccurrence,
    ScheduleOccurrenceObserver,
    SchedulePreview,
    ScheduleRunResolver,
    ScheduleSubmissionResolver,
    ScheduleTargetValidator,
)
from opsmesh.modules.scheduling.events import configuration_event

from .calendar import next_after, normalize_schedule
from .engine import ScheduleExecutionEngine, new_occurrence
from .repository import ScheduleRepository


class PersistentScheduler:
    def __init__(
        self,
        driver: OpsMeshDriver,
        store: VersionedRecordStore,
        authorize: ScheduleAuthorizer,
        dispatcher: ScheduleDispatcher,
        *,
        poll_interval_seconds: float = 1.0,
        lease_seconds: float = 60.0,
        autostart: bool = True,
        agent_resolver: ScheduleAgentResolver | None = None,
        run_resolver: ScheduleRunResolver | None = None,
        validate_target: ScheduleTargetValidator | None = None,
        occurrence_observer: ScheduleOccurrenceObserver | None = None,
        submission_resolver: ScheduleSubmissionResolver | None = None,
    ) -> None:
        if (
            poll_interval_seconds <= 0
            or lease_seconds <= 0
            or not math.isfinite(poll_interval_seconds)
            or not math.isfinite(lease_seconds)
        ):
            raise ValueError("Polling and lease durations must be positive")
        self.driver, self.store, self.authorize = driver, store, authorize
        self.dispatcher = dispatcher
        self.poll_interval_seconds, self.lease_seconds = poll_interval_seconds, lease_seconds
        self.agent_resolver, self.validate_target = agent_resolver, validate_target
        self.repository = ScheduleRepository(store, lease_seconds)
        self.engine = ScheduleExecutionEngine(
            self.repository,
            dispatcher,
            self._validate_execution,
            run_resolver,
            occurrence_observer,
            submission_resolver,
        )
        self.errors = self.engine.errors
        self._task: asyncio.Task[None] | None = None
        if autostart:
            driver.on_startup(self.start)
            driver.on_shutdown(self.stop)

    async def _prepare(self, schedule: PersistentSchedule, actor_id: str) -> PersistentSchedule:
        await self.authorize(actor_id, "schedules.manage", schedule.id)
        if schedule.principal_id != actor_id:
            await self.authorize(actor_id, "schedules:impersonate", schedule.principal_id)
        await self.authorize(schedule.principal_id, "schedules.execute", schedule.id)
        if schedule.agent_revision:
            if self.agent_resolver is None:
                raise OpsMeshError("CONFIG_INVALID", "An Agent catalog resolver is required")
            published = await self.agent_resolver(schedule.agent_revision)
            await self.authorize(actor_id, "agent:run", published.ref)
            await self.authorize(schedule.principal_id, "agent:run", published.ref)
            schedule = schedule.model_copy(update={"agent_revision": published.ref})
        if self.validate_target is not None:
            await self.validate_target(schedule, actor_id)
        elif schedule.reply_target is not None or schedule.run_overrides.model_dump(
            exclude_defaults=True
        ):
            raise OpsMeshError("CONFIG_INVALID", "A schedule resource validator is required")
        return normalize_schedule(schedule)

    async def _validate_execution(self, schedule: PersistentSchedule, actor_id: str) -> None:
        current = await self._prepare(schedule, actor_id)
        if current.agent_revision != schedule.agent_revision:
            raise OpsMeshError("CONFLICT", "Scheduled Agent revision must remain fixed")

    async def create(self, schedule: PersistentSchedule, *, actor_id: str) -> PersistentSchedule:
        schedule = await self._prepare(schedule, actor_id)
        schedule = schedule.model_copy(update={"revision": 1})
        await self.store.create(
            schedule.id,
            {
                "kind": "schedule",
                "schedule": schedule.model_dump(mode="json"),
                "state": "ready",
                "actor_id": actor_id,
            },
            events=(configuration_event(schedule, actor_id, "created"),),
        )
        return schedule

    async def get(self, schedule_id: str, *, actor_id: str) -> PersistentSchedule:
        await self.authorize(actor_id, "schedules.read", schedule_id)
        return PersistentSchedule.model_validate(
            (await self.repository.plan(schedule_id)).data["schedule"]
        )

    async def list_schedules(self, *, actor_id: str) -> list[PersistentSchedule]:
        await self.authorize(actor_id, "schedules.read", "*")
        return [
            PersistentSchedule.model_validate(r.data["schedule"])
            for r in await self.repository.plans()
        ]

    async def _editable(self, schedule_id: str, expected_revision: int) -> StoredRecord:
        record = await self.repository.plan(schedule_id)
        self.repository.check_revision(record, expected_revision)
        if self.repository.lease_active(record):
            raise OpsMeshError(
                "CONFLICT", "Schedule is being dispatched; retry the configuration change"
            )
        return record

    async def update(
        self,
        schedule_id: str,
        schedule: PersistentSchedule,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> PersistentSchedule:
        await self.authorize(actor_id, "schedules.manage", schedule_id)
        if schedule.id != schedule_id:
            raise OpsMeshError("VALIDATION_ERROR", "Schedule IDs cannot be changed")
        record = await self._editable(schedule_id, expected_revision)
        schedule = await self._prepare(schedule, actor_id)
        schedule = schedule.model_copy(update={"revision": expected_revision + 1})
        await self.store.update(
            record.id,
            {
                **record.data,
                "kind": "schedule",
                "schedule": schedule.model_dump(mode="json"),
                "actor_id": actor_id,
                "state": "ready" if schedule.enabled else "paused",
            },
            record.revision,
            events=(configuration_event(schedule, actor_id, "updated"),),
        )
        return schedule

    async def delete(self, schedule_id: str, expected_revision: int, *, actor_id: str) -> None:
        await self.authorize(actor_id, "schedules.manage", schedule_id)
        record = await self._editable(schedule_id, expected_revision)
        schedule = PersistentSchedule.model_validate(record.data["schedule"])
        deleted = schedule.model_copy(update={"enabled": False, "revision": schedule.revision + 1})
        # Tombstones retain evidence and prevent deleted IDs from replaying old trigger keys.
        await self.store.update(
            record.id,
            {
                **record.data,
                "deleted": True,
                "state": "deleted",
                "deleted_by": actor_id,
                "deleted_at": datetime.now(UTC).isoformat(),
                "schedule": schedule.model_copy(
                    update={
                        "enabled": False,
                        "revision": schedule.revision + 1,
                    }
                ).model_dump(mode="json"),
            },
            record.revision,
            events=(configuration_event(deleted, actor_id, "deleted"),),
        )
        for pending in await self.repository.occurrences(schedule_id):
            occurrence = ScheduleOccurrence.model_validate(pending.data["occurrence"])
            if occurrence.status == "queued":
                await self.repository.save_occurrence(
                    pending,
                    occurrence.model_copy(
                        update={
                            "status": "cancelled",
                            "reason": "schedule_deleted",
                            "finished_at": datetime.now(UTC),
                            "updated_at": datetime.now(UTC),
                        }
                    ),
                )

    async def set_enabled(
        self,
        schedule_id: str,
        enabled: bool,
        *,
        actor_id: str,
        expected_revision: int | None = None,
    ) -> PersistentSchedule:
        await self.authorize(actor_id, "schedules.manage", schedule_id)
        schedule = PersistentSchedule.model_validate(
            (await self.repository.plan(schedule_id)).data["schedule"]
        )
        expected = expected_revision if expected_revision is not None else schedule.revision
        if not enabled:
            record = await self._editable(schedule_id, expected)
            disabled = schedule.model_copy(update={"enabled": False, "revision": expected + 1})
            await self.store.update(
                record.id,
                {**record.data, "schedule": disabled.model_dump(mode="json"), "state": "paused"},
                record.revision,
                events=(configuration_event(disabled, actor_id, "updated"),),
            )
            return disabled
        return await self.update(
            schedule_id,
            schedule.model_copy(update={"enabled": enabled}),
            expected,
            actor_id=actor_id,
        )

    async def preview(
        self,
        schedule_id: str,
        expected_revision: int,
        *,
        actor_id: str,
        count: int = 10,
        after: datetime | None = None,
    ) -> SchedulePreview:
        await self.authorize(actor_id, "schedules.read", schedule_id)
        record = await self.repository.plan(schedule_id)
        schedule = self.repository.check_revision(record, expected_revision)
        if not 1 <= count <= 100:
            raise OpsMeshError("VALIDATION_ERROR", "Preview count must be between 1 and 100")
        if after is not None and (after.tzinfo is None or after.utcoffset() is None):
            raise OpsMeshError("VALIDATION_ERROR", "Preview timestamps must include a timezone")
        current: datetime | None = schedule.next_run_at
        if after is not None and current is not None and current <= after:
            current = next_after(schedule, after)
        times: list[datetime] = []
        while current is not None and len(times) < count:
            times.append(current)
            current = next_after(schedule, current)
        return SchedulePreview(
            schedule_id=schedule_id,
            revision=schedule.revision,
            timezone=schedule.timezone,
            times=times,
        )

    async def list_occurrences(
        self,
        schedule_id: str,
        *,
        actor_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScheduleOccurrence]:
        await self.authorize(actor_id, "schedules.read", schedule_id)
        await self.repository.plan(schedule_id, include_deleted=True)
        if not 1 <= limit <= 1000 or offset < 0:
            raise OpsMeshError("VALIDATION_ERROR", "Invalid occurrence pagination")
        rows = await self.repository.occurrences(schedule_id)
        return [
            ScheduleOccurrence.model_validate(r.data["occurrence"])
            for r in list(reversed(rows))[offset : offset + limit]
        ]

    async def run_now(
        self,
        schedule_id: str,
        expected_revision: int,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> ScheduleOccurrence:
        await self.authorize(actor_id, "schedules.manage", schedule_id)
        if not idempotency_key or len(idempotency_key) > 200:
            raise OpsMeshError("VALIDATION_ERROR", "A bounded nonempty idempotency key is required")
        plan = await self.repository.plan(schedule_id)
        schedule = self.repository.check_revision(plan, expected_revision)
        await self._validate_execution(schedule, actor_id)
        key = hashlib.sha256(f"{actor_id}:{idempotency_key}".encode()).hexdigest()[:32]
        occurrence = new_occurrence(schedule, datetime.now(UTC), manual_key=key)
        existing = await self.store.get(occurrence.id)
        if existing is not None:
            return ScheduleOccurrence.model_validate(existing.data["occurrence"])
        claim = await self.repository.claim(schedule_id)
        if claim is None:
            raise OpsMeshError("CONFLICT", "Schedule is being dispatched; retry with the same key")
        claimed, owner = claim
        try:
            self.repository.check_revision(claimed, expected_revision)
            await self.engine.reconcile()
            rows = await self.repository.occurrences(schedule_id)
            queued = sum(r.data["occurrence"]["status"] == "queued" for r in rows)
            active = sum(
                r.data["occurrence"]["status"] in {"submitted", "running", "waiting_approval"}
                for r in rows
            )
            if (
                schedule.concurrency_policy == "skip"
                and active + queued >= schedule.max_active_runs
            ):
                occurrence = occurrence.model_copy(
                    update={
                        "status": "skipped",
                        "reason": "concurrency_limit",
                        "finished_at": datetime.now(UTC),
                    }
                )
                skipped_record = await self.repository.add_occurrence(
                    occurrence, schedule, actor_id
                )
                return ScheduleOccurrence.model_validate(skipped_record.data["occurrence"])
            if queued >= schedule.max_queued_occurrences:
                raise OpsMeshError("RATE_LIMITED", "Schedule occurrence queue is full")
            await self.repository.add_occurrence(occurrence, schedule, actor_id)
            await self.engine.dispatch_pending(schedule_id, owner)
            saved = await self.store.get(occurrence.id)
            assert saved is not None
            return ScheduleOccurrence.model_validate(saved.data["occurrence"])
        finally:
            await self.repository.release(schedule_id, owner)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = self.driver.create_task(self._loop(), name="persistent-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:
                self.engine.record_error("poll", exc)
            await asyncio.sleep(self.poll_interval_seconds)

    async def reconcile(self) -> int:
        return await self.engine.reconcile()

    async def tick(self, *, now: datetime | None = None) -> int:
        if now is not None and (now.tzinfo is None or now.utcoffset() is None):
            raise OpsMeshError("VALIDATION_ERROR", "Tick timestamps must include a timezone")
        horizon = (now or datetime.now(UTC)).astimezone(UTC)
        await self.reconcile()
        dispatched = 0
        for candidate in await self.repository.plans():
            claim = await self.repository.claim(candidate.id)
            if claim is None:
                continue
            record, owner = claim
            try:
                await self.engine.materialize(record, owner, horizon)
                dispatched += await self.engine.dispatch_pending(record.id, owner)
            except OpsMeshError as exc:
                if exc.code not in {"CONFLICT", "SCHEDULE_LEASE_LOST"}:
                    self.engine.record_error(record.id, exc)
            finally:
                await self.repository.release(record.id, owner)
        return dispatched
