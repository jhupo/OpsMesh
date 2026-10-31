"""Append-only audit journal with idempotent event identities."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.audit.api import AuditRecord

_SENSITIVE_FIELDS = frozenset(
    {"password", "password_hash", "token", "api_key", "authorization", "secret"}
)


def redact_details(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if str(key).casefold() in _SENSITIVE_FIELDS
            else redact_details(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_details(item) for item in value]
    return value


class PersistentAuditJournal:
    def __init__(self, database: AsyncDatabase) -> None:
        self._store = VersionedRecordStore(database, "audit_records")

    async def record(
        self,
        action: str,
        resource: str,
        *,
        actor_id: str | None = None,
        outcome: str = "succeeded",
        details: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> AuditRecord:
        record = AuditRecord(
            id=event_id or str(uuid4()),
            actor_id=actor_id,
            action=action,
            resource=resource,
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            details=redact_details(details or {}),
        )
        existing = await self._store.get(record.id)
        if existing:
            prior = AuditRecord.model_validate(existing.data)
            if (prior.actor_id, prior.action, prior.resource, prior.outcome, prior.details) != (
                record.actor_id,
                record.action,
                record.resource,
                record.outcome,
                record.details,
            ):
                raise OpsMeshError("CONFLICT", "Audit identity has different content")
            return prior
        try:
            await self._store.create(record.id, record.model_dump(mode="json"))
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise
            return await self.record(
                action,
                resource,
                actor_id=actor_id,
                outcome=outcome,
                details=details,
                event_id=record.id,
            )
        return record

    async def query(
        self,
        *,
        actor_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        system_only: bool = False,
        action: str | None = None,
        resource: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditRecord]:
        if (
            offset < 0
            or not 1 <= limit <= 1000
            or any(t is not None and t.tzinfo is None for t in (since, until))
        ):
            raise OpsMeshError("INVALID_REQUEST", "Invalid audit pagination or timestamp")
        records = [
            AuditRecord.model_validate(row.data)
            for row in await self._store.list()
            if actor_id is None or row.data["actor_id"] == actor_id
        ]
        selected = [
            item
            for item in reversed(records)
            if (not system_only or item.actor_id is None)
            and (action is None or item.action == action)
            and (resource is None or item.resource == resource)
            and (outcome is None or item.outcome == outcome)
            and (since is None or item.occurred_at >= since)
            and (until is None or item.occurred_at <= until)
        ]
        return selected[offset : offset + limit]
