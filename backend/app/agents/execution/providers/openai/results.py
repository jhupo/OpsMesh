from __future__ import annotations

import json
from typing import Any

from agents import Agent, Tool
from agents import __version__ as agents_sdk_version
from agents.stream_events import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
    StreamEvent,
)
from agents.usage import Usage
from pydantic import TypeAdapter

from backend.app.agents.execution.contracts import (
    AgentRuntimeAgentRef,
    AgentRuntimeContext,
    AgentRuntimeEvent,
    AgentRuntimeHandoffResult,
    AgentRuntimeInterruption,
    AgentRuntimeResumeState,
    AgentRuntimeStreamEvent,
    AgentRuntimeStructuredOutput,
)
from backend.app.agents.execution.providers.openai.mcp import RuntimeMCPServer
from backend.app.agents.execution.providers.openai.tools import OpenAIProductFunctionTool
from backend.app.shared.security.redaction import redact_sensitive_payload


class OpenAIAgentsResultMapper:
    def final_output(self, result: Any) -> tuple[str, AgentRuntimeStructuredOutput | None]:
        value = result.final_output
        if value is None:
            return "", None
        if isinstance(value, str):
            return value, None
        normalized = jsonable(value)
        output_schema = getattr(result.last_agent, "output_type", None)
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
        last_agent = result.last_agent
        if last_agent is not None:
            payload["last_agent"] = str(getattr(last_agent, "name", last_agent))
        usage = result.context_wrapper.usage
        if isinstance(usage, Usage):
            payload["usage"] = TypeAdapter(Usage).dump_python(usage, mode="json")
        return redact_sensitive_payload(payload)

    def resume_state(self, result: Any) -> AgentRuntimeResumeState | None:
        interruptions = result.interruptions
        if not isinstance(interruptions, list | tuple) or not interruptions:
            return None
        state_payload = result.to_state().to_json(
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
        for item in result.interruptions:
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
            review = (
                tool.approval_reviews.get(call_id, {})
                if isinstance(tool, OpenAIProductFunctionTool)
                else {}
            )
            agent = getattr(item, "agent", None)
            servers = agent.mcp_servers if isinstance(agent, Agent) else []
            mcp_server = next(
                (
                    server
                    for server in servers
                    if isinstance(server, RuntimeMCPServer) and tool_name in server.definitions
                ),
                None,
            )
            if mcp_server is not None:
                review = mcp_server.approval_reviews.get(call_id, {})
            mapped.append(
                AgentRuntimeInterruption(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    tool_kind=tool.tool_kind
                    if isinstance(tool, OpenAIProductFunctionTool)
                    else "mcp"
                    if mcp_server is not None
                    else "unknown",
                    arguments=arguments,
                    policy_decision=dict(review),
                )
            )
        return mapped

    def runtime_events(self, result: Any) -> list[AgentRuntimeEvent]:
        usage = result.context_wrapper.usage
        return (
            [
                AgentRuntimeEvent(
                    event_type="model.usage",
                    message="Model usage recorded.",
                    payload={"usage": TypeAdapter(Usage).dump_python(usage, mode="json")},
                )
            ]
            if usage.requests
            else []
        )

    def handoffs(
        self,
        result: Any,
        audits: dict[str, dict[str, object]] | None = None,
    ) -> list[AgentRuntimeHandoffResult]:
        mapped: list[AgentRuntimeHandoffResult] = []
        for item in result.new_items:
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


def runtime_stream_event_from_sdk_item(
    item: StreamEvent,
    *,
    sequence: int,
) -> AgentRuntimeStreamEvent | None:
    delta: object = None
    if isinstance(item, RawResponsesStreamEvent):
        event_type = _openai_raw_stream_event_type(item.data.type)
        delta = getattr(item.data, "delta", None)
        payload = item.data.model_dump(mode="json")
    elif isinstance(item, RunItemStreamEvent):
        event_type = _openai_run_item_event_type(item.name)
        payload = {"name": item.name, "item": jsonable(item.item.raw_item)}
    elif isinstance(item, AgentUpdatedStreamEvent):
        event_type = "agent.updated"
        payload = {"agent": item.new_agent.name}
    else:
        return None
    normalized_payload = redact_sensitive_payload(
        payload if isinstance(payload, dict) else {"value": payload}
    )
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


def _openai_raw_stream_event_type(value: object) -> str:
    if value == "response.output_text.delta":
        return "output.text.delta"
    if isinstance(value, str) and value:
        return f"model.{value}"
    return "model.stream"


def _openai_run_item_event_type(value: object) -> str:
    return {
        "handoff_requested": "agent.handoff.requested",
        "handoff_occured": "agent.handoff",
        "tool_called": "tool.call",
        "tool_output": "tool.result",
        "mcp_approval_requested": "tool.approval_required",
        "mcp_approval_response": "tool.approval_response",
        "message_output_created": "output.message.created",
    }.get(str(value), f"agent.item.{value or 'created'}")


def jsonable(value: object) -> object:
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
    if not isinstance(value, AgentRuntimeContext):
        raise ValueError("SDK state must carry its frozen OpsMesh context")
    payload = TypeAdapter(AgentRuntimeContext).dump_python(value, mode="json")
    if not isinstance(payload, dict):
        raise ValueError("SDK context serialization must produce an object")
    return payload


def _interrupted_tool(item: object, tool_name: str) -> Tool | None:
    agent = getattr(item, "agent", None)
    if not isinstance(agent, Agent):
        return None
    for tool in agent.tools:
        if getattr(tool, "name", None) == tool_name:
            return tool
    return None
