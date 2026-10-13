import json
from uuid import uuid4

from opsmesh.orchestration.tasks.events import TaskEvent
from opsmesh.orchestration.tasks.routes.streaming import _task_event_stream_payload
from opsmesh.shared.http.sse import sse_event


def test_task_event_sse_redacts_credentials() -> None:
    event = TaskEvent(
        id=str(uuid4()),
        workspace_id=uuid4(),
        task_id=uuid4(),
        event_type="task.message.appended",
        payload={"api_key": "sk-hidden", "sequence": 3},
    )

    payload = sse_event("task.event", event.as_dict())

    assert "event: task.event" in payload
    assert "[redacted]" in payload
    assert "sk-hidden" not in payload
    data = json.loads(payload.split("data: ", 1)[1])
    assert data["payload"]["sequence"] == 3


def test_task_event_stream_exposes_durable_client_identity() -> None:
    event_id = str(uuid4())
    event = TaskEvent(
        id=event_id,
        event_id=event_id,
        workspace_id=uuid4(),
        task_id=uuid4(),
        event_type="task.message.appended",
    )

    payload = _task_event_stream_payload(event, cursor=7)

    assert payload["id"] == event_id
    assert payload["event_id"] == event_id
    assert payload["stream"] == {"cursor": 7, "event_cursor": event_id}
