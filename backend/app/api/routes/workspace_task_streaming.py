import asyncio
import json
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from redis import Redis
from redis.exceptions import RedisError

from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.core.config import Settings, get_settings
from backend.app.redis.dependencies import get_redis_client
from backend.app.tasks.events import RedisTaskEventBus, TaskEvent, TaskEventBus

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-resources"])
STREAM_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


def get_task_event_bus(
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> TaskEventBus:
    return RedisTaskEventBus(redis=redis, key_prefix=settings.redis_key_prefix)


def _sse_event(event_name: str, payload: dict[str, object]) -> str:
    redacted = redact_sensitive_payload(payload)
    data = json.dumps(jsonable_encoder(redacted), ensure_ascii=False, sort_keys=True)
    return f"event: {event_name}\ndata: {data}\n\n"


def _task_event_stream_payload(task_event: TaskEvent, cursor: int) -> dict[str, object]:
    payload: dict[str, object] = {
        **task_event.as_dict(),
        "event_id": task_event.event_id or task_event.id,
        "stream": {
            "cursor": cursor,
            "event_cursor": task_event.id,
        },
    }
    outbox_id = task_event.outbox_id or task_event.payload.get("outbox_id")
    if isinstance(outbox_id, str):
        payload["outbox_id"] = outbox_id
    return payload


async def _read_task_bus_events(
    task_event_bus: TaskEventBus,
    *,
    workspace_id: UUID,
    task_id: UUID,
    after_id: str,
    wait_seconds: float,
) -> list[TaskEvent]:
    try:
        return await asyncio.to_thread(
            task_event_bus.read,
            workspace_id=workspace_id,
            task_id=task_id,
            after_id=after_id,
            count=10,
            block_ms=int(max(0, wait_seconds) * 1000),
        )
    except RedisError:
        return []


def _snapshot_messages(snapshot: dict[str, object]) -> list[dict[str, object]]:
    messages = snapshot.get("recent_messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


def _message_sequence(message: dict[str, object], default: int) -> int:
    sequence = message.get("sequence")
    return sequence if isinstance(sequence, int) else default


def _task_stream_complete(snapshot: dict[str, object]) -> bool:
    task = snapshot.get("task")
    summary = snapshot.get("summary")
    if not isinstance(task, dict) or not isinstance(summary, dict):
        return False
    status_value = task.get("status")
    active_runs = summary.get("active_run_count")
    return status_value in STREAM_TERMINAL_TASK_STATUSES and active_runs == 0
