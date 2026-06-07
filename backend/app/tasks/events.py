from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Condition
from typing import Protocol, cast
from uuid import UUID

from redis import Redis

TASK_EVENT_STREAM_MAXLEN = 10_000
RedisStreamEntries = Sequence[
    tuple[
        bytes | str,
        Sequence[tuple[bytes | str, Mapping[bytes | str, bytes | str]]],
    ]
]


@dataclass(frozen=True)
class TaskEvent:
    id: str
    workspace_id: UUID
    task_id: UUID
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None
    outbox_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        event: dict[str, object] = {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at,
        }
        if self.event_id is not None:
            event["event_id"] = self.event_id
        if self.outbox_id is not None:
            event["outbox_id"] = self.outbox_id
        return event


class TaskEventBus(Protocol):
    def publish(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object] | None = None,
        event_id: str | None = None,
        outbox_id: str | None = None,
    ) -> str: ...

    def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
        block_ms: int = 0,
    ) -> list[TaskEvent]: ...


@dataclass(frozen=True)
class RedisTaskEventBus:
    redis: Redis
    key_prefix: str = "chaincloud"
    stream_maxlen: int = TASK_EVENT_STREAM_MAXLEN

    def publish(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object] | None = None,
        event_id: str | None = None,
        outbox_id: str | None = None,
    ) -> str:
        event_payload = _with_event_identity(
            payload,
            event_id=event_id,
            outbox_id=outbox_id,
        )
        fields = {
            "workspace_id": str(workspace_id),
            "task_id": str(task_id),
            "event_type": event_type,
            "payload": json.dumps(event_payload, ensure_ascii=False, sort_keys=True),
            "created_at": datetime.now(UTC).isoformat(),
        }
        if event_id is not None:
            fields["event_id"] = event_id
        if outbox_id is not None:
            fields["outbox_id"] = outbox_id
        stream_id = self.redis.xadd(
            self._stream_key(workspace_id, task_id),
            fields,
            maxlen=self.stream_maxlen,
            approximate=True,
        )
        return _to_text(stream_id)

    def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
        block_ms: int = 0,
    ) -> list[TaskEvent]:
        xread_kwargs = {"count": max(1, count)}
        if block_ms > 0:
            xread_kwargs["block"] = block_ms
        entries = cast(
            RedisStreamEntries,
            self.redis.xread(
                {self._stream_key(workspace_id, task_id): after_id},
                **xread_kwargs,
            ),
        )
        events: list[TaskEvent] = []
        for _, stream_entries in entries:
            for stream_id, fields in stream_entries:
                events.append(
                    _event_from_fields(
                        stream_id=_to_text(stream_id),
                        fields={_to_text(key): _to_text(value) for key, value in fields.items()},
                    )
                )
        return events

    def _stream_key(self, workspace_id: UUID, task_id: UUID) -> str:
        prefix = self.key_prefix.strip(":")
        return f"{prefix}:workspace:{workspace_id}:task:{task_id}:events"


class InMemoryTaskEventBus:
    def __init__(self) -> None:
        self._events: dict[tuple[UUID, UUID], list[TaskEvent]] = defaultdict(list)
        self._condition = Condition()
        self._sequence = 0

    def publish(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object] | None = None,
        event_id: str | None = None,
        outbox_id: str | None = None,
    ) -> str:
        with self._condition:
            self._sequence += 1
            stream_id = f"{int(time.time() * 1000)}-{self._sequence}"
            event_payload = _with_event_identity(
                payload,
                event_id=event_id,
                outbox_id=outbox_id,
            )
            event = TaskEvent(
                id=stream_id,
                workspace_id=workspace_id,
                task_id=task_id,
                event_type=event_type,
                payload=event_payload,
                event_id=event_id,
                outbox_id=outbox_id,
            )
            self._events[(workspace_id, task_id)].append(event)
            self._condition.notify_all()
            return stream_id

    def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
        block_ms: int = 0,
    ) -> list[TaskEvent]:
        deadline = time.monotonic() + (max(0, block_ms) / 1000)
        with self._condition:
            while True:
                events = self._read_now(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    after_id=after_id,
                    count=count,
                )
                if events or block_ms <= 0:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)

    def _read_now(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int,
    ) -> list[TaskEvent]:
        if after_id == "$":
            return []
        events = self._events.get((workspace_id, task_id), [])
        return [
            event
            for event in events
            if _stream_id_gt(event.id, after_id)
        ][: max(1, count)]


def _event_from_fields(*, stream_id: str, fields: dict[str, str]) -> TaskEvent:
    payload = _decode_payload(fields.get("payload"))
    stable_event_id = _identity_value(fields.get("event_id"), payload.get("event_id"))
    outbox_id = _identity_value(fields.get("outbox_id"), payload.get("outbox_id"))
    payload = _with_event_identity(payload, event_id=stable_event_id, outbox_id=outbox_id)
    return TaskEvent(
        id=stream_id,
        workspace_id=UUID(fields["workspace_id"]),
        task_id=UUID(fields["task_id"]),
        event_type=fields["event_type"],
        payload=payload,
        created_at=_decode_datetime(fields.get("created_at")),
        event_id=stable_event_id,
        outbox_id=outbox_id,
    )


def _with_event_identity(
    payload: dict[str, object] | None,
    *,
    event_id: str | None,
    outbox_id: str | None,
) -> dict[str, object]:
    event_payload = dict(payload or {})
    if event_id is not None:
        event_payload["event_id"] = event_id
    if outbox_id is not None:
        event_payload["outbox_id"] = outbox_id
    return event_payload


def _identity_value(primary: str | None, fallback: object) -> str | None:
    if primary:
        return primary
    return fallback if isinstance(fallback, str) and fallback else None


def _decode_payload(raw_payload: str | None) -> dict[str, object]:
    if not raw_payload:
        return {}
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {"raw": raw_payload}
    return payload if isinstance(payload, dict) else {"value": payload}


def _decode_datetime(raw_value: str | None) -> datetime:
    if not raw_value:
        return datetime.now(UTC)
    try:
        value = datetime.fromisoformat(raw_value)
    except ValueError:
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _stream_id_gt(left: str, right: str) -> bool:
    return _stream_id_tuple(left) > _stream_id_tuple(right)


def _stream_id_tuple(value: str) -> tuple[int, int]:
    if value in {"", "0"}:
        return (0, 0)
    if value == "$":
        return (2**63 - 1, 2**63 - 1)
    first, _, second = value.partition("-")
    try:
        return (int(first), int(second or 0))
    except ValueError:
        return (0, 0)


def _to_text(value: bytes | str) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value
