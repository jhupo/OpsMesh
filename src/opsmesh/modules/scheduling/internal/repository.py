"""Plans and occurrence snapshots share the versioned scheduler record collection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.modules.scheduling.api import PersistentSchedule, ScheduleOccurrence
from opsmesh.modules.scheduling.events import occurrence_event

ACTIVE_OCCURRENCES = frozenset({"dispatching", "submitted", "running", "waiting_approval"})
FINISHED_OCCURRENCES = frozenset({"completed", "failed", "blocked", "cancelled", "skipped"})


def is_plan(record: StoredRecord) -> bool:
    # Existing schedule rows are upgraded lazily; no additional SQL table is required.
    return "schedule" in record.data and record.data.get("kind", "schedule") == "schedule"


def occurrence_key(event_id: str) -> str:
    return "_occurrence:" + hashlib.sha256(event_id.encode()).hexdigest()


class ScheduleRepository:
    def __init__(self, store: VersionedRecordStore, lease_seconds: float) -> None:
        self.store, self.lease_seconds = store, lease_seconds

    async def plan(self, schedule_id: str, *, include_deleted: bool = False) -> StoredRecord:
        record = await self.store.get(schedule_id)
        if (
            record is None
            or not is_plan(record)
            or (record.data.get("deleted") and not include_deleted)
        ):
            raise OpsMeshError("NOT_FOUND", "Schedule does not exist")
        return record

    async def plans(self) -> list[StoredRecord]:
        return [r for r in await self.store.list() if is_plan(r) and not r.data.get("deleted")]

    @staticmethod
    def check_revision(record: StoredRecord, expected: int) -> PersistentSchedule:
        schedule = PersistentSchedule.model_validate(record.data["schedule"])
        if schedule.revision != expected:
            raise OpsMeshError("CONFLICT", "Schedule configuration was changed")
        return schedule

    @staticmethod
    def lease_active(record: StoredRecord) -> bool:
        expiry = record.data.get("lease_expires_at")
        return bool(expiry and datetime.fromisoformat(str(expiry)) > datetime.now(UTC))

    async def claim(self, schedule_id: str) -> tuple[StoredRecord, str] | None:
        record = await self.plan(schedule_id)
        if self.lease_active(record):
            return None
        owner = uuid4().hex
        try:
            claimed = await self.store.update(
                schedule_id,
                {**record.data, "lease_owner": owner, "lease_expires_at": self._expiry()},
                record.revision,
            )
        except OpsMeshError as exc:
            if exc.code == "CONFLICT":
                return None
            raise
        return claimed, owner

    def _expiry(self) -> str:
        # Real lease time is deliberately independent from a caller-supplied due horizon.
        return (datetime.now(UTC) + timedelta(seconds=self.lease_seconds)).isoformat()

    async def owned(self, schedule_id: str, owner: str) -> StoredRecord:
        record = await self.plan(schedule_id, include_deleted=True)
        if record.data.get("lease_owner") != owner or not self.lease_active(record):
            raise OpsMeshError("SCHEDULE_LEASE_LOST", "Schedule execution lease was lost")
        return record

    async def renew(self, schedule_id: str, owner: str) -> None:
        record = await self.owned(schedule_id, owner)
        await self.store.update(
            schedule_id, {**record.data, "lease_expires_at": self._expiry()}, record.revision
        )

    async def release(self, schedule_id: str, owner: str) -> None:
        record = await self.plan(schedule_id, include_deleted=True)
        if record.data.get("lease_owner") != owner:
            return
        data = {
            k: v for k, v in record.data.items() if k not in {"lease_owner", "lease_expires_at"}
        }
        try:
            await self.store.update(schedule_id, data, record.revision)
        except OpsMeshError as exc:
            if exc.code != "CONFLICT":
                raise

    async def patch_plan(self, schedule_id: str, owner: str, changes: JsonData) -> None:
        record = await self.owned(schedule_id, owner)
        await self.store.update(schedule_id, {**record.data, **changes}, record.revision)

    async def occurrences(self, schedule_id: str | None = None) -> list[StoredRecord]:
        return [
            r
            for r in await self.store.list()
            if r.data.get("kind") == "occurrence"
            and (schedule_id is None or r.data["occurrence"]["schedule_id"] == schedule_id)
        ]

    async def add_occurrence(
        self, occurrence: ScheduleOccurrence, schedule: PersistentSchedule, actor_id: str
    ) -> StoredRecord:
        existing = await self.store.get(occurrence.id)
        if existing is not None:
            return existing
        try:
            return await self.store.create(
                occurrence.id,
                {
                    "kind": "occurrence",
                    "occurrence": occurrence.model_dump(mode="json"),
                    "configuration": schedule.model_dump(mode="json"),
                    "actor_id": actor_id,
                },
                events=(occurrence_event(occurrence, schedule.principal_id),),
            )
        except OpsMeshError as exc:
            if exc.code != "CONFLICT":
                raise
            existing = await self.store.get(occurrence.id)
            if existing is None:
                raise
            return existing

    async def save_occurrence(
        self, record: StoredRecord, occurrence: ScheduleOccurrence
    ) -> StoredRecord:
        return await self.store.update(
            record.id,
            {**record.data, "occurrence": occurrence.model_dump(mode="json")},
            record.revision,
            events=(
                occurrence_event(occurrence, str(record.data["configuration"]["principal_id"])),
            ),
        )
