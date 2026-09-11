from uuid import uuid4

from backend.app.agent_runtime.contracts import AgentRuntimeEvent
from backend.app.agent_runtime.event_mapping import RuntimeEventTaskMessageMapper
from backend.app.db import models  # noqa: F401
from backend.app.runs.models import AgentRun


def test_runtime_event_messages_redact_body_and_nested_secret_values() -> None:
    run = AgentRun(id=uuid4(), workspace_id=uuid4())
    draft = RuntimeEventTaskMessageMapper().map_event(
        run=run,
        step=None,
        event=AgentRuntimeEvent(
            event_type="tool.failed",
            message="Provider rejected api_key=private-value",
            payload={
                "credential": "private-value",
                "errors": ["Bearer private-value"],
                "details": {"note": "token=private-value"},
                "count": 1,
            },
        ),
    )
    assert draft is not None
    assert draft.body == "[redacted]"
    assert draft.payload["credential"] == "[redacted]"
    assert draft.payload["errors"] == ["[redacted]"]
    assert draft.payload["details"] == {"note": "[redacted]"}
    assert draft.payload["count"] == 1
