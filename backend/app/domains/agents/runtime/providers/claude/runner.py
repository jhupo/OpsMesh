from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterable, AsyncIterator, Callable
from contextlib import suppress
from typing import Protocol, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultError,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)
from claude_agent_sdk import __version__ as claude_sdk_version
from claude_agent_sdk.types import (
    EffortLevel,
    McpServerConfig,
    SessionStore,
    ThinkingConfig,
)
from pydantic import TypeAdapter, ValidationError

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.agents.providers.model_api import ANTHROPIC_MESSAGES_API
from backend.app.domains.agents.runtime.base import BaseSDKAgentRuntimeAdapter
from backend.app.domains.agents.runtime.cancellation import (
    cancel_active_tools,
    raise_if_cancelled,
    stop_cancellation_watcher,
)
from backend.app.domains.agents.runtime.contracts import (
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
)
from backend.app.domains.agents.runtime.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimePolicyError,
    AgentRuntimeProviderError,
)
from backend.app.domains.agents.runtime.guardrails import (
    evaluate_guardrail_stage,
    guardrail_events,
    validated_structured_output,
)
from backend.app.domains.agents.runtime.observer import AgentRuntimeExecutionObserver
from backend.app.domains.agents.runtime.providers.claude.sessions import (
    ClaudeAgentSessionStore,
    _is_rejected_resume,
    _rejected_result,
    _resume_session_id,
    _session_id,
)
from backend.app.domains.agents.runtime.providers.claude.tools import (
    _ApprovalState,
    _claude_hooks,
    _product_tool_name,
    _sdk_tool,
)
from backend.app.domains.agents.runtime.usage import runtime_usage

_EFFORT_SETTING: TypeAdapter[EffortLevel] = TypeAdapter(EffortLevel)
_THINKING_SETTING: TypeAdapter[ThinkingConfig] = TypeAdapter(ThinkingConfig)


class _ClaudeClient(Protocol):
    async def __aenter__(self) -> _ClaudeClient: ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool: ...

    async def query(
        self, prompt: str | AsyncIterable[dict[str, object]], session_id: str = "default"
    ) -> None: ...

    def receive_response(self) -> AsyncIterator[object]: ...

    async def interrupt(self) -> None: ...


class ClaudeAgentSDKRunner(BaseSDKAgentRuntimeAdapter):
    """Claude Agent SDK adapter behind OpsMesh's vendor-neutral runtime contract."""

    capabilities = AgentRuntimeCapabilities(
        provider="anthropic",
        adapter="claude_agent_sdk",
        supported=frozenset(
            {
                AgentRuntimeCapability.TOOLS,
                AgentRuntimeCapability.STRUCTURED_OUTPUT,
                AgentRuntimeCapability.STREAMING,
                AgentRuntimeCapability.RESUMABLE_STATE,
                AgentRuntimeCapability.GUARDRAILS,
                AgentRuntimeCapability.SESSIONS,
                AgentRuntimeCapability.CANCELLATION,
                AgentRuntimeCapability.LIFECYCLE_EVENTS,
                AgentRuntimeCapability.USAGE,
            }
        ),
        limits={"builtin_tools": "disabled", "mcp_server": "opsmesh"},
        unsupported_reasons={
            AgentRuntimeCapability.HANDOFFS.value: (
                "Claude SDK agents are exposed as tools; OpenAI-style handoff "
                "descriptors are not equivalent."
            ),
            AgentRuntimeCapability.AGENTS_AS_TOOLS.value: (
                "Per-subagent MCP execution contexts are not enabled for Claude."
            ),
        },
    )

    def __init__(
        self,
        *,
        client_factory: Callable[[ClaudeAgentOptions], _ClaudeClient] = ClaudeSDKClient,
    ) -> None:
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

    async def _run_once(
        self,
        request: AgentRunRequest,
        observer: AgentRuntimeExecutionObserver,
    ) -> AgentRunResult:
        try:
            return await self._run_once_sdk(request, observer)
        except ClaudeSDKError as exc:
            raise _claude_provider_error(exc) from exc

    async def _run_once_sdk(
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
            resume_existing = await store.has_transcript()
        decisions = {
            (decision.tool_call_id, decision.tool_name): decision
            for decision in request.approval_decisions
        }
        if len(decisions) != len(request.approval_decisions):
            raise ValueError("Claude approval decisions must be unique")
        if _is_rejected_resume(request):
            return _rejected_result(request, self.capabilities)
        approval_state = _ApprovalState(
            reviews={},
            deferred={},
            decisions=decisions,
            active_calls={},
        )
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
            try:
                await client.query(
                    self._prompt_input(request, prompt) if request.attachments else prompt
                )

                async def watch_cancellation() -> None:
                    cancellation = request.cancellation
                    if cancellation is None:
                        return
                    await cancellation.wait_cancelled()
                    cancelled.set()
                    with suppress(Exception):
                        await client.interrupt()
                    with suppress(Exception):
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
            except asyncio.CancelledError:
                with suppress(Exception):
                    await client.interrupt()
                with suppress(Exception):
                    await cancel_active_tools(request)
                raise
        if cancelled.is_set():
            raise AgentRuntimeCancelledError

        result_message = next(
            (message for message in reversed(messages) if isinstance(message, ResultMessage)),
            None,
        )
        if result_message is None:
            raise AgentRuntimeProviderError(
                code="provider_missing_result",
                message="Claude Agent SDK did not return a result message",
            )
        if result_message.is_error and result_message.deferred_tool_use is None:
            raise _claude_result_error(result_message)
        if result_message.terminal_reason in {"aborted_streaming", "aborted_tools"}:
            raise AgentRuntimeCancelledError
        if approval_state.decisions:
            raise ValueError("Claude approval decision was not consumed by the SDK interruption")

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

    async def _prompt_input(
        self, request: AgentRunRequest, prompt: str
    ) -> AsyncIterator[dict[str, object]]:
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for attachment in request.attachments:
            if attachment.kind == "audio":
                continue
            if (
                attachment.content_type.startswith("text/")
                or attachment.content_type == "application/json"
            ):
                continue
            encoded = base64.b64encode(attachment.content).decode("ascii")
            if attachment.kind == "image":
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": attachment.content_type,
                            "data": encoded,
                        },
                    }
                )
            elif attachment.kind == "file":
                content.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": attachment.content_type,
                            "data": encoded,
                        },
                        "title": attachment.filename,
                    }
                )
        yield {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

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
        mcp_servers: dict[str, McpServerConfig] = {}
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
        options = ClaudeAgentOptions(
            tools=[],
            allowed_tools=[],
            system_prompt=request.agent_profile.instructions,
            mcp_servers=mcp_servers,
            strict_mcp_config=True,
            permission_mode="default",
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
            # SDK requires only append/load; its Protocol also lists optional discovery/delete
            # methods. Do not add fake implementations for those product-managed operations.
            session_store=cast(SessionStore, store) if store is not None else None,
            session_store_flush="eager",
            env=env,
        )
        return options

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
                result.structured_output if result.structured_output is not None else result.result,
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


def _string_setting(settings: dict[str, object], key: str) -> str | None:
    value = settings.get(key)
    return value if isinstance(value, str) and value else None


def _effort_setting(settings: dict[str, object]) -> EffortLevel | None:
    value = settings.get("effort")
    if value is None:
        return None
    try:
        return _EFFORT_SETTING.validate_python(value, strict=True)
    except ValidationError:
        raise ValueError("Invalid Claude SDK effort setting") from None


def _thinking_setting(settings: dict[str, object]) -> ThinkingConfig | None:
    value = settings.get("thinking")
    if value is None:
        return None
    try:
        return _THINKING_SETTING.validate_python(value, strict=True)
    except ValidationError:
        raise ValueError("Invalid Claude SDK thinking setting") from None


def _stream_delta(event: dict[str, object]) -> str | None:
    delta = event.get("delta")
    text = delta.get("text") if isinstance(delta, dict) else None
    if isinstance(text, str):
        return text
    if isinstance(delta, str):
        return delta
    return None


def _claude_provider_error(
    exc: ClaudeSDKError,
) -> AgentRuntimeProviderError | AgentRuntimePolicyError:
    if isinstance(exc, CLINotFoundError):
        return AgentRuntimeProviderError(
            code="provider_sdk_unavailable",
            message=str(exc),
            retryable=False,
        )
    if isinstance(exc, CLIJSONDecodeError):
        return AgentRuntimeProviderError(
            code="provider_malformed_response",
            message=str(exc),
            retryable=False,
        )
    if isinstance(exc, ResultError):
        return _claude_api_error(
            message=str(exc),
            status=exc.api_error_status,
            terminal_reason=exc.terminal_reason,
            subtype=exc.subtype,
        )
    if isinstance(exc, CLIConnectionError):
        return AgentRuntimeProviderError(
            code="provider_unavailable",
            message=str(exc),
            retryable=True,
        )
    if isinstance(exc, ProcessError):
        return AgentRuntimeProviderError(
            code="provider_process_failed",
            message=str(exc),
            retryable=False,
        )
    return AgentRuntimeProviderError(
        code="provider_request_failed",
        message=str(exc),
        retryable=False,
    )


def _claude_result_error(
    result: ResultMessage,
) -> AgentRuntimeProviderError | AgentRuntimePolicyError:
    details = "; ".join(result.errors or []) or result.result or "Claude Agent SDK run failed"
    return _claude_api_error(
        message=details,
        status=result.api_error_status,
        terminal_reason=result.terminal_reason,
        subtype=result.subtype,
    )


def _claude_api_error(
    *,
    message: str,
    status: int | None,
    terminal_reason: str | None,
    subtype: str | None,
) -> AgentRuntimeProviderError | AgentRuntimePolicyError:
    if terminal_reason == "max_turns" or subtype == "error_max_turns":
        return AgentRuntimePolicyError(
            code="agent_max_turns_exceeded",
            message="Agent run reached its authorized turn limit",
            event_type="agent.run.max_turns_exceeded",
            metadata={"terminal_reason": terminal_reason, "result_subtype": subtype},
        )
    if status == 429:
        code = "provider_rate_limited"
    elif status is not None and status >= 500:
        code = "provider_unavailable"
    else:
        code = "provider_request_failed"
    retryable = status == 429 or (status is not None and status >= 500)
    if status is None and terminal_reason == "api_error":
        code = "provider_unavailable"
        retryable = True
    return AgentRuntimeProviderError(code=code, message=message, retryable=retryable)
