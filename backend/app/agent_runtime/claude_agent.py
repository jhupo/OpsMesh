from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID, uuid5

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk import __version__ as claude_sdk_version
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    SessionKey,
    SessionStoreEntry,
)

from backend.app.agent_runtime.base import BaseSDKAgentRuntimeAdapter
from backend.app.agent_runtime.cancellation import (
    cancel_active_tools,
    raise_if_cancelled,
    stop_cancellation_watcher,
)
from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeCapabilities,
    AgentRuntimeCapability,
    AgentRuntimeEvent,
    AgentRuntimeGuardrailResult,
    AgentRuntimeInterruption,
    AgentRuntimeResumeState,
    AgentRuntimeStreamEvent,
    AgentRuntimeStructuredOutput,
    AgentRuntimeToolDefinition,
)
from backend.app.agent_runtime.errors import AgentRuntimeCancelledError
from backend.app.agent_runtime.execution_observer import AgentRuntimeExecutionObserver
from backend.app.agent_runtime.guardrails import (
    evaluate_guardrail_stage,
    guardrail_events,
    validated_structured_output,
)
from backend.app.agent_runtime.usage import runtime_usage
from backend.app.core.resilience import CircuitBreakerConfig
from backend.app.model_providers.model_api import ANTHROPIC_MESSAGES_API
from backend.app.model_providers.provider_keys import canonical_model_provider
from backend.app.security.redaction import redact_sensitive_payload

DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG = CircuitBreakerConfig()
_SDK_TOOL_PREFIX = "mcp__opsmesh__"
_SESSION_NAMESPACE = UUID("6bd4b8b9-8a4b-49db-9b6c-d0b7d7da4be6")


class ClaudeAgentSessionStore:
    """Mirror Claude's opaque JSONL transcript into the product session."""

    def __init__(self, session: object, *, session_id: str) -> None:
        self._session = session
        self._session_id = session_id

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        await self._session.add_items(
            [
                {
                    "_opsmesh_runtime": "claude_agent_sdk",
                    "session_id": self._session_id,
                    "project_key": key.get("project_key"),
                    "subpath": key.get("subpath"),
                    "entry": json.loads(json.dumps(entry)),
                }
                for entry in entries
            ]
        )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        items = await self._session.get_items()
        entries: list[SessionStoreEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("_opsmesh_runtime") != "claude_agent_sdk":
                continue
            if item.get("session_id") != self._session_id:
                continue
            if item.get("subpath") != key.get("subpath"):
                continue
            entry = item.get("entry")
            if isinstance(entry, dict):
                entries.append(json.loads(json.dumps(entry)))
        return entries or None


@dataclass
class _ApprovalState:
    reviews: dict[str, dict[str, object]]
    deferred: dict[str, dict[str, object]]
    active_calls: dict[str, str]


class _ClaudeClient(Protocol):
    async def __aenter__(self) -> _ClaudeClient: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool: ...

    async def query(self, prompt: str, session_id: str = "default") -> None: ...

    def receive_response(self) -> AsyncIterator[object]: ...

    async def interrupt(self) -> None: ...


class ClaudeAgentSDKRunner(BaseSDKAgentRuntimeAdapter):
    """Claude Agent SDK adapter behind OpsMesh's vendor-neutral runtime contract."""

    capabilities = AgentRuntimeCapabilities(
        provider="anthropic",
        adapter="claude_agent_sdk",
        supported=frozenset(
            {
                AgentRuntimeCapability.STRUCTURED_OUTPUT,
                AgentRuntimeCapability.STREAMING,
                AgentRuntimeCapability.RESUMABLE_STATE,
                AgentRuntimeCapability.GUARDRAILS,
                AgentRuntimeCapability.SESSIONS,
                AgentRuntimeCapability.CANCELLATION,
            }
        ),
        limits={"builtin_tools": "disabled", "mcp_server": "opsmesh"},
        unsupported_reasons={
            AgentRuntimeCapability.HANDOFFS.value: (
                "Claude SDK agents are exposed as tools; OpenAI-style handoff "
                "descriptors are not equivalent."
            ),
        },
    )

    def __init__(
        self,
        *,
        max_attempts: int = 1,
        circuit_config: CircuitBreakerConfig = DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG,
        client_factory: Callable[[ClaudeAgentOptions], _ClaudeClient] = ClaudeSDKClient,
    ) -> None:
        super().__init__(max_attempts=max_attempts, circuit_config=circuit_config)
        self._client_factory = client_factory

    def _validate_request(self, request: AgentRunRequest) -> None:
        if not request.api_key:
            raise ValueError("Anthropic provider requires an explicit provider API key")
        if request.handoffs:
            raise NotImplementedError(
                "Claude Agent SDK supports agents-as-tools, not OpenAI-style handoff descriptors"
            )
        if request.handoff_agents:
            raise NotImplementedError(
                "Claude Agent SDK agent definitions are not enabled until "
                "their MCP tool scope is explicit"
            )
        if request.agent_tools:
            raise NotImplementedError(
                "Claude Agent SDK subagents are not enabled until their per-agent "
                "MCP execution contexts can be enforced"
            )
        if request.resume_state is not None and request.resume_state.provider != "claude_agent_sdk":
            raise ValueError("Claude Agent SDK runner cannot restore another provider's state")

    def _provider_circuit_key(self, request: AgentRunRequest) -> str:
        return _model_provider_circuit_key(request)

    async def _run_once(
        self,
        request: AgentRunRequest,
        observer: AgentRuntimeExecutionObserver,
    ) -> AgentRunResult:
        session_id = _session_id(request)
        store = (
            ClaudeAgentSessionStore(request.session, session_id=session_id)
            if request.session is not None
            else None
        )
        resume_existing = False
        if store is not None and request.resume_state is None:
            resume_existing = (
                await store.load({"project_key": "opsmesh", "session_id": session_id}) is not None
            )
        if _is_rejected_resume(request):
            return _rejected_result(request)

        approval_state = _ApprovalState(reviews={}, deferred={}, active_calls={})
        if request.approval_decisions:
            state_payload = json.loads(request.resume_state.serialized_state)
            approval_state.active_calls[state_payload["tool_name"]] = state_payload["tool_call_id"]
        observer.lifecycle(
            "agent.started",
            "Claude agent started.",
            {"agent": request.agent_profile.name},
        )
        options = self._options(
            request,
            session_id,
            store,
            approval_state,
            observer,
            resume_existing,
        )
        prompt = self._input_for_request(request)
        guardrail_results: list[AgentRuntimeGuardrailResult] = []
        if request.guardrails is not None and request.resume_state is None:
            evaluate_guardrail_stage(
                request.guardrails.input,
                prompt,
                stage="input",
                results=guardrail_results,
            )
        messages: list[object] = []
        cancelled = asyncio.Event()
        await raise_if_cancelled(request.cancellation)
        client = self._client_factory(options)
        async with client:
            await client.query(prompt)

            async def watch_cancellation() -> None:
                cancellation = request.cancellation
                if cancellation is None:
                    return
                await cancellation.wait_cancelled()
                cancelled.set()
                await client.interrupt()
                await cancel_active_tools(request)

            watcher: asyncio.Task[object] | None = None
            if request.cancellation is not None:
                watcher = asyncio.create_task(watch_cancellation())
            try:
                async for message in client.receive_response():
                    messages.append(message)
                    if request.stream and isinstance(message, StreamEvent):
                        mapped = self._stream_event(
                            message,
                            sequence=len(observer.stream_events) + 1,
                        )
                        observer.stream(
                            mapped.event_type,
                            payload=mapped.payload,
                            delta=mapped.delta,
                        )
            finally:
                await stop_cancellation_watcher(watcher)
        if cancelled.is_set():
            raise AgentRuntimeCancelledError

        result_message = next(
            (message for message in reversed(messages) if isinstance(message, ResultMessage)),
            None,
        )
        if result_message is None:
            raise RuntimeError("Claude Agent SDK did not return a result message")
        if result_message.is_error and result_message.deferred_tool_use is None:
            details = "; ".join(result_message.errors or []) or "Claude Agent SDK run failed"
            raise RuntimeError(details)
        if result_message.terminal_reason in {"aborted_streaming", "aborted_tools"}:
            raise AgentRuntimeCancelledError

        interruptions = self._interruptions(request, result_message, approval_state)
        resume = self._resume_state(result_message, interruptions)
        final_output, structured = self._final_output(
            request,
            result_message,
            messages,
            validate_output=not interruptions,
        )
        if request.guardrails is not None and not interruptions:
            evaluate_guardrail_stage(
                request.guardrails.output,
                structured.value if structured is not None else final_output,
                stage="output",
                results=guardrail_results,
            )
        events = self._events(request, result_message, messages, approval_state)
        observer.lifecycle(
            "agent.completed",
            "Claude agent completed.",
            {"agent": request.agent_profile.name},
        )
        events.extend(guardrail_events(guardrail_results))
        return AgentRunResult(
            final_output=final_output,
            raw_output=self._safe_raw_output(request, result_message),
            events=tuple(events),
            resume_state=resume,
            interruptions=tuple(interruptions),
            structured_output=structured,
            guardrail_results=tuple(guardrail_results),
            usage=runtime_usage(
                result_message.usage,
                model_usage=result_message.model_usage,
                total_cost_usd=result_message.total_cost_usd,
                request_count=result_message.num_turns,
            ),
        )

    def _options(
        self,
        request: AgentRunRequest,
        session_id: str,
        store: ClaudeAgentSessionStore | None,
        approval_state: _ApprovalState,
        observer: AgentRuntimeExecutionObserver,
        resume_existing: bool = False,
    ) -> ClaudeAgentOptions:
        tool_defs = tuple(request.context.tool_definitions)
        sdk_tools = [_sdk_tool(definition, request, approval_state) for definition in tool_defs]
        mcp_servers = {}
        if sdk_tools:
            mcp_servers["opsmesh"] = create_sdk_mcp_server(
                name="opsmesh",
                version="0.1.0",
                tools=sdk_tools,
            )
        model_settings = request.agent_profile.model_settings or {}
        env = {
            "ANTHROPIC_API_KEY": request.api_key or "",
            "CLAUDE_AGENT_SDK_CLIENT_APP": "opsmesh/0.1.0",
        }
        if request.base_url:
            env["ANTHROPIC_BASE_URL"] = request.base_url.rstrip("/")
        resume_id = _resume_session_id(request) or (session_id if resume_existing else None)
        approved_resume = bool(request.approval_decisions and resume_id)
        return ClaudeAgentOptions(
            tools=[],
            allowed_tools=[],
            system_prompt=request.agent_profile.instructions,
            mcp_servers=mcp_servers,
            strict_mcp_config=True,
            permission_mode="bypassPermissions" if approved_resume else "default",
            resume=resume_id,
            session_id=None if resume_id else session_id,
            max_turns=request.max_turns,
            model=request.model or request.agent_profile.model,
            fallback_model=_string_setting(model_settings, "fallback_model"),
            effort=_effort_setting(model_settings),
            thinking=_thinking_setting(model_settings),
            output_format=(
                {"type": "json_schema", "schema": dict(request.output_schema.schema)}
                if request.output_schema is not None
                else None
            ),
            include_partial_messages=request.stream,
            hooks=_claude_hooks(request, approval_state, observer, bool(sdk_tools)),
            setting_sources=[],
            skills=[],
            session_store=store,
            session_store_flush="eager",
            env=env,
        )

    def _input_for_request(self, request: AgentRunRequest) -> str:
        if not request.continuations:
            return request.input_text
        continuation_lines = [
            "- "
            + json.dumps(
                {
                    "tool_name": continuation.tool_name,
                    "status": continuation.status,
                    "result": continuation.result
                    if continuation.status == "completed"
                    else continuation.error,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            for continuation in request.continuations
        ]
        return (
            request.input_text
            + "\n\nCompleted runtime tool results:\n"
            + "\n".join(continuation_lines)
        )

    def _interruptions(
        self,
        request: AgentRunRequest,
        result: ResultMessage,
        approval_state: _ApprovalState,
    ) -> list[AgentRuntimeInterruption]:
        deferred = result.deferred_tool_use
        if deferred is None:
            return []
        tool_name = _product_tool_name(deferred.name)
        definition = next(
            (item for item in request.context.tool_definitions if item.name == tool_name),
            None,
        )
        review = approval_state.reviews.get(deferred.id, {})
        if definition is not None and not review:
            review = {"decision": "require_approval", "risk_level": definition.risk_level}
        return [
            AgentRuntimeInterruption(
                tool_call_id=deferred.id,
                tool_name=tool_name,
                tool_kind=definition.source if definition is not None else "unknown",
                arguments=dict(deferred.input),
                policy_decision=redact_sensitive_payload(dict(review)),
                message="Tool call requires approval before Claude can continue.",
                metadata={"provider_tool_name": deferred.name},
            )
        ]

    def _resume_state(
        self,
        result: ResultMessage,
        interruptions: list[AgentRuntimeInterruption],
    ) -> AgentRuntimeResumeState | None:
        if not interruptions:
            return None
        return AgentRuntimeResumeState(
            provider="claude_agent_sdk",
            serialized_state=json.dumps(
                {
                    "session_id": result.session_id,
                    "tool_call_id": interruptions[0].tool_call_id,
                    "tool_name": interruptions[0].tool_name,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            schema_version="1",
            sdk_version=claude_sdk_version,
        )

    def _final_output(
        self,
        request: AgentRunRequest,
        result: ResultMessage,
        messages: list[object],
        *,
        validate_output: bool = True,
    ) -> tuple[str, AgentRuntimeStructuredOutput | None]:
        if request.output_schema is not None and validate_output:
            structured = validated_structured_output(
                request.output_schema,
                result.structured_output
                if result.structured_output is not None
                else result.result,
            )
            return (
                json.dumps(structured.value, ensure_ascii=False, sort_keys=True),
                structured,
            )
        if isinstance(result.result, str):
            return result.result, None
        text = "\n".join(
            block.text
            for message in messages
            if isinstance(message, AssistantMessage)
            for block in message.content
            if isinstance(block, TextBlock)
        )
        return text, None

    def _events(
        self,
        request: AgentRunRequest,
        result: ResultMessage,
        messages: list[object],
        approval_state: _ApprovalState,
    ) -> list[AgentRuntimeEvent]:
        events: list[AgentRuntimeEvent] = [
            AgentRuntimeEvent(
                event_type="model.request",
                message="Claude Agent SDK request metadata recorded.",
                payload={
                    "model_provider": {
                        "provider": request.provider or "anthropic",
                        "model": request.model or request.agent_profile.model,
                        "model_api": ANTHROPIC_MESSAGES_API,
                        "credential_id": str(request.model_provider_credential_id)
                        if request.model_provider_credential_id is not None
                        else None,
                    }
                },
            )
        ]
        if result.usage is not None or result.total_cost_usd is not None:
            events.append(
                AgentRuntimeEvent(
                    event_type="model.usage",
                    message="Model usage recorded.",
                    payload=redact_sensitive_payload(
                        {
                            "usage": result.usage,
                            "model_usage": result.model_usage,
                            "total_cost_usd": result.total_cost_usd,
                        }
                    ),
                )
            )
        for message in messages:
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        events.append(
                            AgentRuntimeEvent(
                                event_type="tool.call",
                                message="Claude Agent SDK requested a runtime tool.",
                                payload={
                                    "tool_name": _product_tool_name(block.name),
                                    "tool_call_id": block.id,
                                    "arguments": redact_sensitive_payload(dict(block.input)),
                                },
                            )
                        )
            if hasattr(message, "content"):
                for block in getattr(message, "content", []) or []:
                    if isinstance(block, ToolResultBlock):
                        events.append(
                            AgentRuntimeEvent(
                                event_type="tool.result",
                                message="Claude Agent SDK returned a runtime tool result.",
                                payload={
                                    "tool_call_id": block.tool_use_id,
                                    "status": "failed" if block.is_error else "completed",
                                },
                            )
                        )
        if approval_state.deferred:
            events.append(
                AgentRuntimeEvent(
                    event_type="tool.approval_required",
                    message="Claude Agent SDK paused for runtime approval.",
                    payload=redact_sensitive_payload(dict(approval_state.deferred)),
                )
            )
        events.append(
            AgentRuntimeEvent(
                event_type="model.completed",
                message="Claude Agent SDK run completed.",
                payload={
                    "session_id": result.session_id,
                    "terminal_reason": result.terminal_reason,
                },
            )
        )
        return events

    def _stream_event(
        self,
        message: StreamEvent,
        *,
        sequence: int,
    ) -> AgentRuntimeStreamEvent:
        event = dict(message.event)
        delta = _stream_delta(event)
        return AgentRuntimeStreamEvent(
            sequence=sequence,
            event_type="output.text.delta" if delta else "model.stream",
            payload=redact_sensitive_payload(event),
            delta=delta,
        )

    def _safe_raw_output(
        self,
        request: AgentRunRequest,
        result: ResultMessage,
    ) -> dict[str, object]:
        return redact_sensitive_payload(
            {
                "provider": request.provider or "anthropic",
                "adapter": "claude_agent_sdk",
                "model": request.model or request.agent_profile.model,
                "model_api": ANTHROPIC_MESSAGES_API,
                "session_id": result.session_id,
                "result": result.result,
                "structured_output": result.structured_output,
                "usage": result.usage,
                "model_usage": result.model_usage,
                "total_cost_usd": result.total_cost_usd,
                "terminal_reason": result.terminal_reason,
            }
        )


def _sdk_tool(
    definition: AgentRuntimeToolDefinition,
    request: AgentRunRequest,
    approval_state: _ApprovalState,
) -> object:
    sdk_name = definition.name

    @tool(sdk_name, definition.description, dict(definition.input_schema))
    async def invoke(arguments: dict[str, object]) -> dict[str, object]:
        await raise_if_cancelled(request.cancellation)
        executor = request.tool_executor
        if executor is None:
            return _tool_error("No runtime tool executor is configured")
        try:
            sdk_executor = getattr(executor, "execute_sdk_tool", None)
            if callable(sdk_executor):
                tool_call_id = approval_state.active_calls.get(definition.name)
                if not tool_call_id:
                    return _tool_error("Claude tool call is missing its runtime call ID")
                result = sdk_executor(
                    context=request.context,
                    tool_name=definition.name,
                    arguments=arguments,
                    tool_call_id=tool_call_id,
                )
            else:
                result = executor.execute_tool(
                    context=request.context,
                    tool_name=definition.name,
                    arguments=arguments,
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
            return _tool_error(str(exc))

    return invoke


def _claude_hooks(
    request: AgentRunRequest,
    approval_state: _ApprovalState,
    observer: AgentRuntimeExecutionObserver,
    has_tools: bool,
) -> dict[str, list[HookMatcher]]:
    hooks: dict[str, list[HookMatcher]] = {
        "Stop": [
            HookMatcher(
                hooks=[_claude_lifecycle_hook("agent.stop", request, observer)]
            )
        ]
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
            hooks=[
                _claude_lifecycle_hook("agent.tool.completed", request, observer)
            ],
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
) -> Callable[[HookInput, str | None, HookContext], object]:
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
) -> Callable[[HookInput, str | None, HookContext], object]:
    async def review(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
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
        executor = request.tool_executor
        reviewer = getattr(executor, "review_tool_call", None)
        review: object
        if callable(reviewer):
            review = reviewer(
                context=request.context,
                tool_name=product_name,
                arguments=dict(input_data.get("tool_input") or {}),
            )
        else:
            review = {
                "decision": "require_approval" if definition.requires_approval else "allow",
                "risk_level": definition.risk_level,
                "reasons": [
                    "tool.manifest.requires_approval"
                    if definition.requires_approval
                    else "tool.manifest.auto_allow"
                ],
            }
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
        if call_id:
            approval_state.active_calls[product_name] = call_id
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
        if decision != "require_approval":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Tool allowed by OpsMesh policy",
                }
            }
        if call_id:
            approval_state.deferred[call_id] = {
                "tool_name": product_name,
                "arguments": redact_sensitive_payload(dict(input_data.get("tool_input") or {})),
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


def _product_tool_name(name: str) -> str:
    return name[len(_SDK_TOOL_PREFIX) :] if name.startswith(_SDK_TOOL_PREFIX) else name


def _session_id(request: AgentRunRequest) -> str:
    if request.session is None:
        return str(uuid5(_SESSION_NAMESPACE, str(request.context.run_id)))
    return str(
        uuid5(
            _SESSION_NAMESPACE,
            f"{request.context.workspace_id}:{request.session.session_id}",
        )
    )


def _resume_session_id(request: AgentRunRequest) -> str | None:
    if request.resume_state is None:
        return None
    try:
        payload = json.loads(request.resume_state.serialized_state)
    except json.JSONDecodeError as exc:
        raise ValueError("Stored Claude Agent SDK state is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
        raise ValueError("Stored Claude Agent SDK state is missing session_id")
    expected = _session_id(request)
    if payload["session_id"] != expected:
        raise ValueError("Stored Claude Agent SDK state does not match the product session")
    return payload["session_id"]


def _is_rejected_resume(request: AgentRunRequest) -> bool:
    if not request.approval_decisions:
        return False
    if request.resume_state is None:
        raise ValueError("Approval decisions require a Claude Agent SDK resume state")
    payload = json.loads(request.resume_state.serialized_state)
    call_id = payload.get("tool_call_id") if isinstance(payload, dict) else None
    tool_name = payload.get("tool_name") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("session_id") != _session_id(request):
        raise ValueError("Stored Claude Agent SDK state does not match the product session")
    for decision in request.approval_decisions:
        if decision.tool_call_id != call_id or decision.tool_name != tool_name:
            raise ValueError("Stored approval decision does not match the Claude SDK interruption")
        if decision.status not in {"approved", "rejected"}:
            raise ValueError("Stored tool approval decision is invalid")
    return any(decision.status == "rejected" for decision in request.approval_decisions)


def _rejected_result(request: AgentRunRequest) -> AgentRunResult:
    decision = request.approval_decisions[0]
    return AgentRunResult(
        final_output=decision.reason or f"Tool {decision.tool_name} was rejected by policy.",
        events=(
            AgentRuntimeEvent(
                event_type="tool.rejected",
                message="Tool approval was rejected; Claude session was not resumed.",
                payload={"tool_name": decision.tool_name, "tool_call_id": decision.tool_call_id},
            ),
        ),
        capabilities=ClaudeAgentSDKRunner.capabilities,
    )


def _model_provider_circuit_key(request: AgentRunRequest) -> str:
    provider = canonical_model_provider(request.provider or "anthropic")
    host = "anthropic-default"
    if request.base_url:
        parsed = urlparse(request.base_url)
        host = parsed.netloc or parsed.path or host
    credential = (
        str(request.model_provider_credential_id)
        if request.model_provider_credential_id
        else "no-credential"
    )
    model = request.model or request.agent_profile.model
    return f"model-provider:{provider}:{host}:{credential}:{ANTHROPIC_MESSAGES_API}:{model}"


def _string_setting(settings: dict[str, object], key: str) -> str | None:
    value = settings.get(key)
    return value if isinstance(value, str) and value else None


def _effort_setting(settings: dict[str, object]) -> str | None:
    value = settings.get("effort")
    return value if value in {"low", "medium", "high", "xhigh", "max"} else None


def _thinking_setting(settings: dict[str, object]) -> dict[str, object] | None:
    value = settings.get("thinking")
    if isinstance(value, dict) and value.get("type") in {"adaptive", "enabled", "disabled"}:
        return dict(value)
    return None


def _stream_delta(event: dict[str, object]) -> str | None:
    delta = event.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("text"), str):
        return delta["text"]
    if isinstance(delta, str):
        return delta
    return None
