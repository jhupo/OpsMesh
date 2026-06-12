import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime
from threading import Condition
from uuid import uuid4

import fakeredis
from redis.exceptions import ConnectionError

from backend.app.api.routes.workspace_resources import (
    _read_task_bus_events,
    _sse_event,
    _task_event_stream_payload,
)
from backend.app.tasks.events import RedisTaskEventBus, TaskEvent


def test_redis_task_event_bus_publishes_and_reads_stream_events() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    bus = RedisTaskEventBus(redis=redis, key_prefix="opsmesh")
    workspace_id = uuid4()
    task_id = uuid4()
    event_identity = str(uuid4())
    outbox_id = str(uuid4())

    event_id = bus.publish(
        workspace_id=workspace_id,
        task_id=task_id,
        event_type="task.message.appended",
        payload={"sequence": 3, "authorization": "Bearer hidden"},
        event_id=event_identity,
        outbox_id=outbox_id,
    )
    events = bus.read(
        workspace_id=workspace_id,
        task_id=task_id,
        after_id="0-0",
    )

    assert len(events) == 1
    assert events[0].id == event_id
    assert events[0].workspace_id == workspace_id
    assert events[0].task_id == task_id
    assert events[0].event_type == "task.message.appended"
    assert events[0].event_id == event_identity
    assert events[0].outbox_id == outbox_id
    assert events[0].payload == {
        "sequence": 3,
        "authorization": "Bearer hidden",
        "event_id": event_identity,
        "outbox_id": outbox_id,
    }


def test_in_memory_task_event_bus_respects_task_scope_and_cursor() -> None:
    bus = InMemoryTaskEventBus()
    workspace_id = uuid4()
    task_id = uuid4()
    other_task_id = uuid4()

    first_id = bus.publish(
        workspace_id=workspace_id,
        task_id=task_id,
        event_type="task.started",
    )
    bus.publish(
        workspace_id=workspace_id,
        task_id=other_task_id,
        event_type="task.other",
    )
    second_id = bus.publish(
        workspace_id=workspace_id,
        task_id=task_id,
        event_type="task.progress",
    )

    events = bus.read(
        workspace_id=workspace_id,
        task_id=task_id,
        after_id=first_id,
    )

    assert [event.id for event in events] == [second_id]
    assert [event.event_type for event in events] == ["task.progress"]
    assert bus.read(workspace_id=workspace_id, task_id=task_id, after_id="$") == []


def test_task_event_sse_payload_is_redacted() -> None:
    bus = InMemoryTaskEventBus()
    workspace_id = uuid4()
    task_id = uuid4()
    bus.publish(
        workspace_id=workspace_id,
        task_id=task_id,
        event_type="task.message.appended",
        payload={"api_key": "sk-hidden"},
    )
    event = bus.read(workspace_id=workspace_id, task_id=task_id, after_id="0-0")[0]

    payload = _sse_event("task.event", event.as_dict())

    assert "event: task.event" in payload
    assert "[redacted]" in payload
    assert "sk-hidden" not in payload


def test_task_event_stream_payload_exposes_stable_client_identity() -> None:
    bus = InMemoryTaskEventBus()
    workspace_id = uuid4()
    task_id = uuid4()
    event_identity = str(uuid4())
    outbox_id = str(uuid4())
    stream_id = bus.publish(
        workspace_id=workspace_id,
        task_id=task_id,
        event_type="task.message.appended",
        payload={"api_key": "sk-hidden"},
        event_id=event_identity,
        outbox_id=outbox_id,
    )
    event = bus.read(workspace_id=workspace_id, task_id=task_id, after_id="0-0")[0]

    payload = _task_event_stream_payload(event, cursor=7)

    assert payload["id"] == stream_id
    assert payload["event_id"] == event_identity
    assert payload["outbox_id"] == outbox_id
    assert payload["stream"] == {"cursor": 7, "event_cursor": stream_id}


def test_task_event_stream_reader_falls_back_when_redis_is_unavailable() -> None:
    workspace_id = uuid4()
    task_id = uuid4()

    events = asyncio.run(
        _read_task_bus_events(
            _FailingTaskEventBus(),
            workspace_id=workspace_id,
            task_id=task_id,
            after_id="$",
            wait_seconds=0,
        )
    )

    assert events == []


class _FailingTaskEventBus:
    def read(self, **_: object) -> object:
        raise ConnectionError("redis unavailable")


class InMemoryTaskEventBus:
    def __init__(self) -> None:
        self._events: dict[tuple[object, object], list[TaskEvent]] = defaultdict(list)
        self._condition = Condition()
        self._sequence = 0

    def publish(
        self,
        *,
        workspace_id: object,
        task_id: object,
        event_type: str,
        payload: dict[str, object] | None = None,
        event_id: str | None = None,
        outbox_id: str | None = None,
    ) -> str:
        with self._condition:
            self._sequence += 1
            stream_id = f"{int(time.time() * 1000)}-{self._sequence}"
            event_payload = dict(payload or {})
            if event_id is not None:
                event_payload["event_id"] = event_id
            if outbox_id is not None:
                event_payload["outbox_id"] = outbox_id
            self._events[(workspace_id, task_id)].append(
                TaskEvent(
                    id=stream_id,
                    workspace_id=workspace_id,  # type: ignore[arg-type]
                    task_id=task_id,  # type: ignore[arg-type]
                    event_type=event_type,
                    payload=event_payload,
                    created_at=datetime.now(UTC),
                    event_id=event_id,
                    outbox_id=outbox_id,
                )
            )
            self._condition.notify_all()
            return stream_id

    def read(
        self,
        *,
        workspace_id: object,
        task_id: object,
        after_id: str,
        count: int = 10,
        block_ms: int = 0,
    ) -> list[TaskEvent]:
        deadline = time.monotonic() + (max(0, block_ms) / 1000)
        with self._condition:
            while True:
                events = [
                    event
                    for event in self._events.get((workspace_id, task_id), [])
                    if after_id != "$" and _stream_id_gt(event.id, after_id)
                ][: max(1, count)]
                if events or block_ms <= 0:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=remaining)


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
