from __future__ import annotations

from typing import Any

from backend.app.agent_runtime.contracts import AgentRuntimeEvent
from backend.app.security.redaction import redact_sensitive_payload


class OpenAIAgentsResultMapper:
    def safe_raw_output(self, result: Any) -> dict[str, object]:
        payload: dict[str, object] = {"final_output": str(getattr(result, "final_output", ""))}
        sdk_continuation: dict[str, object] = {
            "provider": "openai_agents",
            "mode": "sdk_continuation_snapshot",
            "native_tool_call_continuation": False,
        }
        last_response_id = getattr(result, "last_response_id", None)
        if isinstance(last_response_id, str) and last_response_id:
            payload["last_response_id"] = last_response_id
            sdk_continuation["last_response_id"] = last_response_id
        conversation_id = getattr(result, "conversation_id", None)
        if isinstance(conversation_id, str) and conversation_id:
            payload["conversation_id"] = conversation_id
            sdk_continuation["conversation_id"] = conversation_id
        last_agent = getattr(result, "last_agent", None)
        if last_agent is not None:
            payload["last_agent"] = str(getattr(last_agent, "name", last_agent))
        self._capture_resume_input(result, payload, sdk_continuation)
        self._capture_run_state(result, payload, sdk_continuation)
        usage = getattr(result, "usage", None)
        if usage is not None:
            payload["usage"] = jsonable(usage)
        payload["sdk_continuation"] = sdk_continuation
        return redact_sensitive_payload(payload)

    def runtime_events(self, result: Any) -> list[AgentRuntimeEvent]:
        events: list[AgentRuntimeEvent] = []
        last_agent = getattr(result, "last_agent", None)
        if last_agent is not None:
            events.append(
                AgentRuntimeEvent(
                    event_type="agent.handoff",
                    message="Run finished with agent handoff state.",
                    payload={"target_agent": str(getattr(last_agent, "name", last_agent))},
                )
            )
        usage = getattr(result, "usage", None)
        if usage is not None:
            events.append(
                AgentRuntimeEvent(
                    event_type="model.usage",
                    message="Model usage recorded.",
                    payload={"usage": jsonable(usage)},
                )
            )
        raw_events = getattr(result, "events", None)
        if isinstance(raw_events, list | tuple):
            for item in raw_events:
                event = runtime_event_from_sdk_item(item)
                if event is not None:
                    events.append(event)
        return events

    def _capture_resume_input(
        self,
        result: Any,
        payload: dict[str, object],
        sdk_continuation: dict[str, object],
    ) -> None:
        to_input_list = getattr(result, "to_input_list", None)
        if not callable(to_input_list):
            return
        try:
            resume_input = jsonable(to_input_list(mode="normalized"))
            payload["resume_input"] = resume_input
            sdk_continuation["resume_input"] = resume_input
        except Exception as exc:  # pragma: no cover - SDK internals are best-effort.
            payload["resume_input_error"] = type(exc).__name__
            sdk_continuation["resume_input_error"] = type(exc).__name__

    def _capture_run_state(
        self,
        result: Any,
        payload: dict[str, object],
        sdk_continuation: dict[str, object],
    ) -> None:
        to_state = getattr(result, "to_state", None)
        if not callable(to_state):
            return
        try:
            state = to_state()
            to_json = getattr(state, "to_json", None)
            if callable(to_json):
                run_state_json = str(to_json())
                payload["run_state_json"] = run_state_json
                sdk_continuation["run_state_json"] = run_state_json
        except Exception as exc:  # pragma: no cover - SDK internals are best-effort.
            payload["run_state_error"] = type(exc).__name__
            sdk_continuation["run_state_error"] = type(exc).__name__


def runtime_event_from_sdk_item(item: object) -> AgentRuntimeEvent | None:
    event_type = getattr(item, "type", None) or getattr(item, "event_type", None)
    if not isinstance(event_type, str) or not event_type:
        return None
    payload = jsonable(item)
    return AgentRuntimeEvent(
        event_type=event_type,
        message=str(getattr(item, "message", "") or event_type),
        payload=redact_sensitive_payload(
            payload if isinstance(payload, dict) else {"value": payload}
        ),
    )


def jsonable(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return jsonable(dumped)
    return str(value)
