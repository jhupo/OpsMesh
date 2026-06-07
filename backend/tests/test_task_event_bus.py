import asyncio
from uuid import uuid4

import fakeredis
from redis.exceptions import ConnectionError

from backend.app.api.routes.workspace_resources import (
    _read_task_bus_events,
    _sse_event,
    _task_event_stream_payload,
)
from backend.app.tasks.events import InMemoryTaskEventBus, RedisTaskEventBus


def test_redis_task_event_bus_publishes_and_reads_stream_events() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    bus = RedisTaskEventBus(redis=redis, key_prefix="chaincloud")
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
