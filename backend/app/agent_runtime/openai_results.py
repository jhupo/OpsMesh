from __future__ import annotations

import json
from typing import Any

from agents import __version__ as agents_sdk_version

from backend.app.agent_runtime.contracts import (
    AgentRuntimeAgentRef,
    AgentRuntimeEvent,
    AgentRuntimeHandoffResult,
    AgentRuntimeInterruption,
    AgentRuntimeResumeState,
    AgentRuntimeStreamEvent,
    AgentRuntimeStructuredOutput,
)
from backend.app.security.redaction import redact_sensitive_payload


class OpenAIAgentsResultMapper:
    def final_output(self, result: Any) -> tuple[str, AgentRuntimeStructuredOutput | None]:
        value = getattr(result, "final_output", None)
        if value is None:
            return "", None
        if isinstance(value, str):
            return value, None
        normalized = jsonable(value)
        output_schema = getattr(result, "_current_agent_output_schema", None)
        schema_name = None
        if output_schema is not None:
            name = getattr(output_schema, "name", None)
            if callable(name):
                candidate = name()
                schema_name = candidate if isinstance(candidate, str) else None
        return (
            json.dumps(normalized, ensure_ascii=False, sort_keys=True),
            AgentRuntimeStructuredOutput(
                value=normalized,
                schema_name=schema_name,
                validated=output_schema is not None,
            ),
        )

    def safe_raw_output(self, result: Any) -> dict[str, object]:
        final_output, _ = self.final_output(result)
        payload: dict[str, object] = {"final_output": final_output}
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
        usage = getattr(result, "usage", None)
        if usage is not None:
            payload["usage"] = jsonable(usage)
        payload["sdk_continuation"] = sdk_continuation
        return redact_sensitive_payload(payload)

    def resume_state(self, result: Any) -> AgentRuntimeResumeState | None:
        interruptions = getattr(result, "interruptions", None)
        if not isinstance(interruptions, list | tuple) or not interruptions:
            return None
        to_state = getattr(result, "to_state", None)
        if not callable(to_state):
            raise ValueError("Interrupted OpenAI Agents run did not expose resumable state")
        state = to_state()
        to_json = getattr(state, "to_json", None)
        if not callable(to_json):
            raise ValueError("Interrupted OpenAI Agents run state is not serializable")
        state_payload = to_json(
            context_serializer=_serialize_runtime_context,
            strict_context=True,
            include_tracing_api_key=False,
        )
        if not isinstance(state_payload, dict):
            raise ValueError("Interrupted OpenAI Agents run state is invalid")
        schema_version = state_payload.get("$schemaVersion")
        return AgentRuntimeResumeState(
            provider="openai_agents",
            serialized_state=json.dumps(
                state_payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            schema_version=str(schema_version) if schema_version is not None else None,
            sdk_version=agents_sdk_version,
        )

    def interruptions(self, result: Any) -> list[AgentRuntimeInterruption]:
        mapped: list[AgentRuntimeInterruption] = []
        for item in getattr(result, "interruptions", ()) or ():
            call_id = getattr(item, "call_id", None)
            tool_name = getattr(item, "name", None)
            raw_arguments = getattr(item, "arguments", None)
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("OpenAI Agents interruption is missing a tool call ID")
            if not isinstance(tool_name, str) or not tool_name:
                raise ValueError("OpenAI Agents interruption is missing a tool name")
            try:
                arguments = json.loads(raw_arguments or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("OpenAI Agents interruption arguments are invalid") from exc
            if not isinstance(arguments, dict):
                raise ValueError("OpenAI Agents interruption arguments must be an object")
            tool = _interrupted_tool(item, tool_name)
            reviews = getattr(tool, "_opsmesh_approval_reviews", {})
            review = reviews.get(call_id, {}) if isinstance(reviews, dict) else {}
            mapped.append(
                AgentRuntimeInterruption(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    tool_kind=str(getattr(tool, "_opsmesh_tool_kind", "unknown")),
                    arguments=arguments,
                    policy_decision=dict(review) if isinstance(review, dict) else {},
                )
            )
        return mapped

    def runtime_events(self, result: Any) -> list[AgentRuntimeEvent]:
        events: list[AgentRuntimeEvent] = []
        last_agent = getattr(result, "last_agent", None)
        new_items = getattr(result, "new_items", ()) or ()
        has_handoff_item = any(
            getattr(item, "type", None) == "handoff_output_item" for item in new_items
        )
        if last_agent is not None and not has_handoff_item:
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

    def stream_events(self, result: Any) -> list[AgentRuntimeStreamEvent]:
        """Map buffered SDK stream items without exposing SDK event instances."""

        raw_events = getattr(result, "stream_events", None)
        if not isinstance(raw_events, list | tuple):
            raw_events = getattr(result, "events", None)
        if not isinstance(raw_events, list | tuple):
            return []
        mapped: list[AgentRuntimeStreamEvent] = []
        for sequence, item in enumerate(raw_events, start=1):
            event = runtime_stream_event_from_sdk_item(item, sequence=sequence)
            if event is not None:
                mapped.append(event)
        return mapped

    def handoffs(
        self,
        result: Any,
        audits: dict[str, dict[str, object]] | None = None,
    ) -> list[AgentRuntimeHandoffResult]:
        mapped: list[AgentRuntimeHandoffResult] = []
        for item in getattr(result, "new_items", ()) or ():
            if getattr(item, "type", None) != "handoff_output_item":
                continue
            source_agent = getattr(item, "source_agent", None)
            target_agent = getattr(item, "target_agent", None)
            source_name = str(getattr(source_agent, "name", source_agent or ""))
            target_name = str(getattr(target_agent, "name", target_agent or ""))
            audit = dict((audits or {}).get(target_name, {}))
            filtered_keys = audit.pop("filtered_context_keys", ())
            mapped.append(
                AgentRuntimeHandoffResult(
                    source=AgentRuntimeAgentRef(name=source_name),
                    target=AgentRuntimeAgentRef(name=target_name),
                    status="completed",
                    filtered_context_keys=(
                        tuple(str(item) for item in filtered_keys)
                        if isinstance(filtered_keys, tuple | list)
                        else ()
                    ),
                    metadata=redact_sensitive_payload(audit),
                )
            )
        return mapped

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


def runtime_stream_event_from_sdk_item(
    item: object,
    *,
    sequence: int,
) -> AgentRuntimeStreamEvent | None:
    event_type = getattr(item, "type", None) or getattr(item, "event_type", None)
    if not isinstance(event_type, str) or not event_type:
        return None
    payload = jsonable(item)
    normalized_payload = redact_sensitive_payload(
        payload if isinstance(payload, dict) else {"value": payload}
    )
    delta = getattr(item, "delta", None)
    if not isinstance(delta, str):
        delta = getattr(item, "text", None)
    if not isinstance(delta, str):
        delta = None
    if delta is not None:
        redacted_delta = redact_sensitive_payload({"value": delta}).get("value")
        delta = redacted_delta if isinstance(redacted_delta, str) else str(redacted_delta)
    return AgentRuntimeStreamEvent(
        sequence=sequence,
        event_type=event_type,
        payload=normalized_payload,
        delta=delta,
        is_terminal=event_type in {"run.completed", "run.failed", "error"},
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


def _serialize_runtime_context(value: object) -> dict[str, object]:
    return {
        key: str(item) if key.endswith("_id") and item is not None else item
        for key, item in {
            "workspace_id": getattr(value, "workspace_id", None),
            "task_id": getattr(value, "task_id", None),
            "run_id": getattr(value, "run_id", None),
            "allowed_tools": list(getattr(value, "allowed_tools", ())),
        }.items()
    }


def _interrupted_tool(item: object, tool_name: str) -> object | None:
    agent = getattr(item, "agent", None)
    for tool in getattr(agent, "tools", ()) or ():
        if getattr(tool, "name", None) == tool_name:
            return tool
    return None
