from __future__ import annotations

import json
from dataclasses import dataclass

from claude_agent_sdk import SdkMcpTool, tool
from claude_agent_sdk.types import (
    HookCallback,
    HookContext,
    HookEvent,
    HookInput,
    HookJSONOutput,
    HookMatcher,
)

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.agents.runtime.cancellation import raise_if_cancelled
from backend.app.domains.agents.runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeApprovalDecision,
    AgentRuntimeToolDefinition,
)
from backend.app.domains.agents.runtime.errors import (
    AgentRuntimeCancelledError,
    normalize_agent_error,
)
from backend.app.domains.agents.runtime.observer import AgentRuntimeExecutionObserver

_SDK_TOOL_PREFIX = "mcp__opsmesh__"


@dataclass
class _ApprovalState:
    reviews: dict[str, dict[str, object]]
    deferred: dict[str, dict[str, object]]
    decisions: dict[tuple[str, str], AgentRuntimeApprovalDecision]
    active_calls: dict[tuple[str, str], list[str]]


def _sdk_tool(
    definition: AgentRuntimeToolDefinition,
    request: AgentRunRequest,
    approval_state: _ApprovalState,
) -> SdkMcpTool[dict[str, object]]:
    sdk_name = definition.name

    @tool(sdk_name, definition.description, dict(definition.input_schema))
    async def invoke(arguments: dict[str, object]) -> dict[str, object]:
        await raise_if_cancelled(request.cancellation)
        executor = request.tool_executor
        if executor is None:
            return _tool_error("No runtime tool executor is configured")
        try:
            tool_call_id = _consume_active_call(
                approval_state,
                definition.name,
                arguments,
            )
            if not tool_call_id:
                return _tool_error("Claude tool call is missing its runtime call ID")
            result = await executor.execute_tool(
                context=request.context,
                tool_name=definition.name,
                arguments=arguments,
                tool_call_id=tool_call_id,
                approval_granted=True,
            )
            await raise_if_cancelled(request.cancellation)
            if result.status == "completed":
                payload = result.output or {}
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(payload, ensure_ascii=False),
                        }
                    ]
                }
            return _tool_error(
                json.dumps(result.error or {"code": "tool_failed"}, ensure_ascii=False)
            )
        except AgentRuntimeCancelledError:
            raise
        except Exception as exc:
            return _tool_error(normalize_agent_error(exc).message)

    return invoke


def _claude_hooks(
    request: AgentRunRequest,
    approval_state: _ApprovalState,
    observer: AgentRuntimeExecutionObserver,
    has_tools: bool,
) -> dict[HookEvent, list[HookMatcher]]:
    hooks: dict[HookEvent, list[HookMatcher]] = {
        "Stop": [HookMatcher(hooks=[_claude_lifecycle_hook("agent.stop", request, observer)])]
    }
    if not has_tools:
        return hooks
    hooks["PreToolUse"] = [
        HookMatcher(
            matcher=f"{_SDK_TOOL_PREFIX}.*",
            hooks=[
                _approval_hook(request, approval_state),
                _claude_lifecycle_hook("agent.tool.started", request, observer),
            ],
        )
    ]
    hooks["PostToolUse"] = [
        HookMatcher(
            matcher=f"{_SDK_TOOL_PREFIX}.*",
            hooks=[_claude_lifecycle_hook("agent.tool.completed", request, observer)],
        )
    ]
    hooks["PostToolUseFailure"] = [
        HookMatcher(
            matcher=f"{_SDK_TOOL_PREFIX}.*",
            hooks=[_claude_lifecycle_hook("agent.tool.failed", request, observer)],
        )
    ]
    return hooks


def _claude_lifecycle_hook(
    event_type: str,
    request: AgentRunRequest,
    observer: AgentRuntimeExecutionObserver,
) -> HookCallback:
    async def record(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        if event_type == "agent.tool.started":
            await raise_if_cancelled(request.cancellation)
        tool_name = input_data.get("tool_name")
        payload: dict[str, object] = {"agent": request.agent_profile.name}
        if isinstance(tool_name, str) and tool_name:
            payload["tool_name"] = _product_tool_name(tool_name)
        if tool_use_id:
            payload["tool_call_id"] = tool_use_id
        observer.lifecycle(
            event_type,
            event_type.replace(".", " ").capitalize() + ".",
            payload,
        )
        return {}

    return record


def _approval_hook(
    request: AgentRunRequest,
    approval_state: _ApprovalState,
) -> HookCallback:
    async def review(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        if input_data["hook_event_name"] != "PreToolUse":
            raise ValueError("Tool approval requires a PreToolUse hook event")
        tool_name = str(input_data.get("tool_name") or "")
        call_id = tool_use_id or str(input_data.get("tool_use_id") or "")
        product_name = _product_tool_name(tool_name)
        definition = next(
            (item for item in request.context.tool_definitions if item.name == product_name),
            None,
        )
        if definition is None or product_name not in request.context.allowed_tools:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Tool is not present in the authorized run manifest"
                    ),
                }
            }
        resumed_decision = approval_state.decisions.pop((call_id, product_name), None)
        if resumed_decision is not None:
            if resumed_decision.status == "approved":
                _activate_call(
                    approval_state,
                    product_name,
                    dict(input_data["tool_input"]),
                    call_id,
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "permissionDecisionReason": "Tool approved by OpsMesh",
                    }
                }
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": resumed_decision.reason
                    or "Tool rejected by OpsMesh",
                }
            }
        executor = request.tool_executor
        if executor is None:
            raise ValueError("Claude tool execution requires a runtime tool executor")
        review: object = executor.review_tool_call(
            context=request.context,
            tool_name=product_name,
            arguments=dict(input_data["tool_input"]),
        )
        if not isinstance(review, dict):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Tool approval review returned an invalid decision",
                }
            }
        decision = review.get("decision")
        approval_state.reviews[call_id] = dict(review)
        if decision == "deny":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": str(
                        review.get("reason") or "Tool denied by policy"
                    ),
                }
            }
        if decision == "allow":
            if call_id:
                _activate_call(
                    approval_state,
                    product_name,
                    dict(input_data["tool_input"]),
                    call_id,
                )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Tool allowed by OpsMesh policy",
                }
            }
        if decision != "require_approval":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Tool approval review returned an invalid decision",
                }
            }
        if call_id:
            approval_state.deferred[call_id] = {
                "tool_name": product_name,
                "arguments": redact_sensitive_payload(dict(input_data["tool_input"])),
            }
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "defer",
                "permissionDecisionReason": (
                    "OpsMesh policy requires approval before tool execution"
                ),
            }
        }

    return review


def _tool_error(message: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def _activate_call(
    state: _ApprovalState,
    tool_name: str,
    arguments: dict[str, object],
    call_id: str,
) -> None:
    if not call_id:
        return
    state.active_calls.setdefault(_active_call_key(tool_name, arguments), []).append(call_id)


def _consume_active_call(
    state: _ApprovalState,
    tool_name: str,
    arguments: dict[str, object],
) -> str | None:
    key = _active_call_key(tool_name, arguments)
    call_ids = state.active_calls.get(key)
    if not call_ids:
        return None
    call_id = call_ids.pop(0)
    if not call_ids:
        state.active_calls.pop(key, None)
    return call_id


def _active_call_key(tool_name: str, arguments: dict[str, object]) -> tuple[str, str]:
    return (
        tool_name,
        json.dumps(arguments, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
    )


def _product_tool_name(name: str) -> str:
    return name[len(_SDK_TOOL_PREFIX) :] if name.startswith(_SDK_TOOL_PREFIX) else name
