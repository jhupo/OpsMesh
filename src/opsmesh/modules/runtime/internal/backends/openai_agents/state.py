"""Version-bound approval checkpoints without serialized runtime services."""

import hashlib
import json
from typing import Any

from agents import RunContextWrapper, RunState

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentRunCompleted,
    AgentRunInterrupted,
    BackendOutcome,
    PendingToolApproval,
)
from opsmesh.framework.errors import OpsMeshError

from .serialization import serialize_sdk_value
from .usage import observe_usage


def pending_approval(item: Any) -> PendingToolApproval:
    raw = serialize_sdk_value(item.raw_item)
    call_id = str(raw.get("call_id") or raw.get("id"))
    identity = [item.agent.name, item.tool_name, raw.get("server_label"), call_id]
    approval_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
    arguments = raw.get("arguments", raw.get("action", raw.get("operation", {})))
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    return PendingToolApproval(
        id=approval_id,
        tool_name=item.tool_name or "unknown",
        agent_name=item.agent.name,
        arguments=arguments,
        server_label=raw.get("server_label"),
    )


async def restore_input(
    agent: Any,
    input: Any,
    context: AgentExecutionContext,
    checkpoint: dict[str, Any] | None,
    decisions: dict[str, bool] | None,
    sdk_version: str,
    graph: dict[str, str],
) -> Any:
    if checkpoint is None:
        return input
    if checkpoint.get("sdk_version") != sdk_version or checkpoint.get("graph") != graph:
        raise OpsMeshError("CHECKPOINT_INCOMPATIBLE", "SDK or agent graph changed")
    state = await RunState.from_json(
        agent, checkpoint["state"], context_override=RunContextWrapper(context)
    )
    for item in state.get_interruptions():
        approval_id = pending_approval(item).id
        if approval_id in (decisions or {}):
            if (decisions or {})[approval_id]:
                state.approve(item)
            else:
                state.reject(item)
    return state


def capture_outcome(
    result: Any, context: AgentExecutionContext, sdk_version: str, graph: dict[str, str]
) -> BackendOutcome:
    usage = observe_usage(result)
    if not context.state.usage_snapshot.get("usage_available", True):
        usage = usage.model_copy(update={"usage_available": False})
    if result.interruptions:
        return AgentRunInterrupted(
            pending=tuple(pending_approval(item) for item in result.interruptions),
            checkpoint={
                "sdk_version": sdk_version,
                "graph": graph,
                "state": redact_tool_credentials(
                    result.to_state().to_json(
                        context_serializer=lambda _: context.snapshot(),
                        include_tracing_api_key=False,
                    )
                ),
            },
            usage=usage,
        )
    return AgentRunCompleted(
        output=serialize_sdk_value(result.final_output),
        usage=usage,
        history=tuple(serialize_sdk_value(item) for item in result.to_input_list()),
    )


def redact_tool_credentials(value: Any) -> Any:
    """The pinned SDK serializes Hosted MCP metadata; restore rebinds the live tool by label."""
    if isinstance(value, list):
        return [redact_tool_credentials(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: redact_tool_credentials(item) for key, item in value.items()}
    if isinstance(result.get("mcp_tool"), dict):
        tool = result["mcp_tool"]
        if isinstance(tool.get("tool_config"), dict):
            tool["tool_config"].pop("authorization", None)
            tool["tool_config"].pop("headers", None)
    return result
