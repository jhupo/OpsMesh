"""Transactional event persistence, leases, bounded retries and dead letters."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    insert,
    or_,
    select,
)
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData
from opsmesh.infrastructure.database.transactions import database_connection, serialized_write
from opsmesh.infrastructure.telemetry.tracing import current_trace_carrier


def normalize_event(event: JsonData) -> JsonData:
    result = dict(event)
    event_type = result.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise OpsMeshError("EVENT_INVALID", "Outbox event type is required")
    result.setdefault("source", event_type.split(".", 1)[0])
    result.setdefault("schema_version", 1)
    result.setdefault("occurred_at", datetime.now(UTC).isoformat())
    result.setdefault("id", str(uuid4()))
    result.setdefault("trace_context", current_trace_carrier())
    event_id = result["id"]
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
        raise OpsMeshError("EVENT_INVALID", "Outbox event id must be 1 to 128 characters")
    payload = result.get("payload", {})
    if not isinstance(payload, dict):
        raise OpsMeshError("EVENT_INVALID", "Outbox payload must be an object")
    payload = dict(payload)
    subject = result.get("subject")
    if isinstance(subject, dict):
        for key, value in subject.items():
            payload.setdefault(key, value)
    elif subject is not None:
        payload.setdefault("subject", subject)
    result["payload"] = payload
    if not isinstance(result["source"], str) or not result["source"]:
        raise OpsMeshError("EVENT_INVALID", "Outbox source is required")
    if type(result["schema_version"]) is not int or result["schema_version"] < 1:
        raise OpsMeshError("EVENT_INVALID", "Event schema version must be positive")
    try:
        if datetime.fromisoformat(result["occurred_at"]).tzinfo is None:
            raise ValueError("Event timestamps require a timezone")
        normalized: JsonData = json.loads(json.dumps(result, allow_nan=False))
        return normalized
    except (ValueError, TypeError, RecursionError) as error:
        raise OpsMeshError(
            "EVENT_INVALID", "Event must contain a valid timestamp and JSON data"
        ) from error


class TransactionalOutbox:
    def __init__(self, engine: AsyncEngine, metadata: MetaData) -> None:
        self._engine = engine
        self.table = Table(
            "opsmesh_outbox",
            metadata,
            Column("id", String(128), primary_key=True),
            Column("event", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("delivered_at", DateTime(timezone=True), nullable=True),
            Column("attempts", Integer, nullable=False),
            Column("available_at", DateTime(timezone=True), nullable=False),
            Column("lease_owner", String(128), nullable=True),
            Column("lease_until", DateTime(timezone=True), nullable=True),
            Column("dead_at", DateTime(timezone=True), nullable=True),
            Column("last_error", String(1024), nullable=True),
        )
        Index(
            "ix_opsmesh_outbox_ready",
            self.table.c.delivered_at,
            self.table.c.dead_at,
            self.table.c.available_at,
            self.table.c.lease_until,
        )

    async def enqueue(self, connection: AsyncConnection, events: Sequence[JsonData]) -> None:
        for event in events:
            normalized = normalize_event(event)
            now = datetime.now(UTC)
            await connection.execute(
                insert(self.table).values(
                    id=normalized["id"],
                    event=normalized,
                    created_at=now,
                    attempts=0,
                    available_at=now,
                )
            )

    def _ready(self, now: datetime) -> Any:
        return and_(
            self.table.c.delivered_at.is_(None),
            self.table.c.dead_at.is_(None),
            self.table.c.available_at <= now,
            or_(self.table.c.lease_until.is_(None), self.table.c.lease_until <= now),
        )

    @staticmethod
    def _validate_claim(worker_id: str, limit: int, lease_seconds: float) -> None:
        if (
            not 1 <= len(worker_id) <= 128
            or not 1 <= limit <= 1000
            or not 0 < lease_seconds <= 3600
        ):
            raise OpsMeshError("CONFIG_INVALID", "Invalid outbox owner, limit or lease duration")

    async def pending(self, limit: int = 100) -> list[JsonData]:
        self._validate_claim("reader", limit, 1)
        async with database_connection(self._engine) as connection:
            rows = await connection.execute(
                select(self.table.c.event)
                .where(self._ready(datetime.now(UTC)))
                .order_by(self.table.c.created_at, self.table.c.id)
                .limit(limit)
            )
            return [dict(row.event) for row in rows]

    async def claim(
        self, worker_id: str, limit: int = 100, lease_seconds: float = 30, max_attempts: int = 5
    ) -> list[JsonData]:
        self._validate_claim(worker_id, limit, lease_seconds)
        if max_attempts < 1:
            raise OpsMeshError("CONFIG_INVALID", "Outbox attempts must be positive")
        now = datetime.now(UTC)
        claimed: list[JsonData] = []
        async with serialized_write(self._engine) as connection:
            await connection.execute(
                sql_update(self.table)
                .where(self._ready(now), self.table.c.attempts >= max_attempts)
                .values(
                    dead_at=now,
                    lease_owner=None,
                    lease_until=None,
                    last_error="Maximum delivery attempts exceeded",
                )
            )
            candidate_query = (
                select(self.table.c.id)
                .where(self._ready(now))
                .order_by(self.table.c.created_at, self.table.c.id)
                .limit(limit)
            )
            if self._engine.dialect.name == "postgresql":
                candidate_query = candidate_query.with_for_update(skip_locked=True)
            candidates = await connection.execute(candidate_query)
            for row in candidates:
                result = await connection.execute(
                    sql_update(self.table)
                    .where(self.table.c.id == row.id, self._ready(now))
                    .values(
                        lease_owner=worker_id,
                        lease_until=now + timedelta(seconds=lease_seconds),
                        attempts=self.table.c.attempts + 1,
                    )
                )
                if result.rowcount == 1:
                    event: JsonData = (
                        await connection.execute(
                            select(self.table.c.event).where(self.table.c.id == row.id)
                        )
                    ).scalar_one()
                    claimed.append(dict(event))
        return claimed

    def _owned(self, event_id: str, owner: str | None, now: datetime) -> Any:
        lease = (
            self.table.c.lease_owner.is_(None)
            if owner is None
            else and_(self.table.c.lease_owner == owner, self.table.c.lease_until > now)
        )
        return and_(
            self.table.c.id == event_id,
            self.table.c.delivered_at.is_(None),
            self.table.c.dead_at.is_(None),
            lease,
        )

    async def acknowledge(self, event_id: str, owner: str | None = None) -> None:
        now = datetime.now(UTC)
        async with serialized_write(self._engine) as connection:
            result = await connection.execute(
                sql_update(self.table)
                .where(self._owned(event_id, owner, now))
                .values(delivered_at=now, lease_owner=None, lease_until=None)
            )
            if result.rowcount != 1:
                delivered = (
                    await connection.execute(
                        select(self.table.c.delivered_at).where(self.table.c.id == event_id)
                    )
                ).scalar_one_or_none()
                if delivered is None:
                    raise OpsMeshError(
                        "OUTBOX_LEASE_LOST", "Event is not owned by this delivery worker"
                    )

    async def reject(
        self,
        event_id: str,
        owner: str | None = None,
        *,
        max_attempts: int = 5,
        error: str = "Delivery failed",
        retry_base_seconds: float = 1,
    ) -> None:
        if max_attempts < 1 or not 0 <= retry_base_seconds <= 300:
            raise OpsMeshError("CONFIG_INVALID", "Invalid retry policy")
        now = datetime.now(UTC)
        async with serialized_write(self._engine) as connection:
            row = (
                await connection.execute(
                    select(self.table.c.attempts).where(self._owned(event_id, owner, now))
                )
            ).first()
            if row is None:
                raise OpsMeshError("OUTBOX_LEASE_LOST", "Cannot reject an event without its lease")
            attempts = max(1, row.attempts)
            dead = attempts >= max_attempts
            delay = min(300, retry_base_seconds * 2 ** min(attempts - 1, 20))
            result = await connection.execute(
                sql_update(self.table)
                .where(self._owned(event_id, owner, now))
                .values(
                    lease_owner=None,
                    lease_until=None,
                    attempts=attempts,
                    last_error=error[:1024],
                    dead_at=now if dead else None,
                    available_at=now + timedelta(seconds=delay),
                )
            )
            if result.rowcount != 1:
                raise OpsMeshError("OUTBOX_LEASE_LOST", "Event lease changed during rejection")

    async def renew(self, event_id: str, owner: str, lease_seconds: float = 30) -> None:
        self._validate_claim(owner, 1, lease_seconds)
        now = datetime.now(UTC)
        async with serialized_write(self._engine) as connection:
            result = await connection.execute(
                sql_update(self.table)
                .where(self._owned(event_id, owner, now))
                .values(lease_until=now + timedelta(seconds=lease_seconds))
            )
            if result.rowcount != 1:
                raise OpsMeshError(
                    "OUTBOX_LEASE_LOST", "Cannot renew an expired or reassigned lease"
                )

    async def dead_letters(self, limit: int = 100) -> list[JsonData]:
        self._validate_claim("reader", limit, 1)
        async with database_connection(self._engine) as connection:
            rows = await connection.execute(
                select(self.table)
                .where(self.table.c.dead_at.is_not(None))
                .order_by(self.table.c.created_at, self.table.c.id)
                .limit(limit)
            )
            return [
                {
                    "id": row.id,
                    "event": row.event,
                    "attempts": row.attempts,
                    "last_error": row.last_error,
                    "dead_at": row.dead_at.isoformat(),
                }
                for row in rows
            ]

    async def inspect(
        self, *, status: str = "dead", limit: int = 100, offset: int = 0
    ) -> list[JsonData]:
        self._validate_claim("reader", limit, 1)
        if offset < 0 or status not in {"dead", "pending", "delivered", "all"}:
            raise OpsMeshError("INVALID_REQUEST", "Invalid outbox query")
        query = select(self.table)
        if status == "dead":
            query = query.where(self.table.c.dead_at.is_not(None))
        elif status == "delivered":
            query = query.where(self.table.c.delivered_at.is_not(None))
        elif status == "pending":
            query = query.where(self.table.c.dead_at.is_(None), self.table.c.delivered_at.is_(None))
        async with database_connection(self._engine) as connection:
            rows = await connection.execute(
                query.order_by(self.table.c.created_at.desc(), self.table.c.id)
                .limit(limit)
                .offset(offset)
            )
            return [
                {
                    "id": row.id,
                    "type": row.event["type"],
                    "source": row.event["source"],
                    "attempts": row.attempts,
                    "created_at": row.created_at.isoformat(),
                    "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
                    "dead_at": row.dead_at.isoformat() if row.dead_at else None,
                    "lease_until": row.lease_until.isoformat() if row.lease_until else None,
                    "available_at": row.available_at.isoformat(),
                    "last_error": row.last_error,
                }
                for row in rows
            ]

    async def requeue(self, event_id: str) -> None:
        async with serialized_write(self._engine) as connection:
            result = await connection.execute(
                sql_update(self.table)
                .where(self.table.c.id == event_id, self.table.c.dead_at.is_not(None))
                .values(
                    dead_at=None,
                    attempts=0,
                    available_at=datetime.now(UTC),
                    lease_owner=None,
                    lease_until=None,
                )
            )
            if result.rowcount != 1:
                raise OpsMeshError("NOT_FOUND", "Dead-letter event not found")
