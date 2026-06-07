import json
from typing import Any, Literal
from urllib.parse import urlparse

from agents import (
    Agent,
    ModelSettings,
    RunConfig,
    RunContextWrapper,
    Runner,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrail,
    function_tool,
)
from agents.models.interface import Model
from agents.models.openai_provider import OpenAIProvider

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeEvent,
    AgentRuntimeToolExecutor,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.core.resilience import CircuitBreakerConfig, async_retry_with_circuit
from backend.app.model_providers.base_url import normalize_openai_compatible_base_url
from backend.app.model_providers.model_api import (
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
    canonical_model_api,
)
from backend.app.model_providers.provider_keys import model_provider_key
from backend.app.security.redaction import redact_sensitive_payload

DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG = CircuitBreakerConfig()


class OpenAIAgentsRunner:
    def __init__(
        self,
        *,
        max_attempts: int = 1,
        circuit_config: CircuitBreakerConfig = DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._circuit_config = circuit_config

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        agent = self._build_agent(request)
        result = await async_retry_with_circuit(
            key=_model_provider_circuit_key(request),
            func=lambda: Runner.run(
                agent,
                self._input_for_request(request),
                context=request.context,
                max_turns=request.max_turns,
                run_config=self._run_config(request),
                previous_response_id=request.previous_response_id,
                conversation_id=request.conversation_id,
                session=request.session,
            ),
            max_attempts=self._max_attempts,
            circuit_config=self._circuit_config,
            should_retry=lambda exc: normalize_agent_error(exc).retryable,
        )
        return AgentRunResult(
            final_output=str(result.final_output),
            raw_output=self._safe_raw_output(result),
            events=tuple(self._runtime_events(result)),
        )

    def _build_agent(self, request: AgentRunRequest) -> Agent[Any]:
        profile = request.agent_profile
        model_name = request.model or profile.model
        model: str | Model = model_name
        if request.api_key is not None or request.base_url is not None:
            model = OpenAIProvider(
                api_key=request.api_key,
                base_url=normalize_openai_compatible_base_url(request.base_url),
                use_responses=_use_responses_api(request.model_api),
            ).get_model(model_name)
        return Agent(
            name=profile.name,
            instructions=profile.instructions,
            model=model,
            model_settings=self._model_settings(profile.model_settings),
            tools=self._tools(request),
        )

    def _tools(self, request: AgentRunRequest) -> list[Any]:
        if request.tool_executor is None:
            return []
        return [
            self._mcp_function_tool(tool_name, request.tool_executor)
            for tool_name in request.context.allowed_tools
        ]

    def _mcp_function_tool(
        self,
        tool_name: str,
        executor: AgentRuntimeToolExecutor,
    ) -> Any:
        async def call_mcp_tool(
            ctx: RunContextWrapper[Any],
            arguments: dict[str, object],
        ) -> dict[str, object]:
            result = executor.execute_tool(
                context=ctx.context,
                tool_name=tool_name,
                arguments=arguments,
            )
            if result.status == "completed":
                return _tool_response_with_metadata(
                    tool_name=tool_name,
                    status=result.status,
                    payload=result.output or {},
                    metadata=result.metadata,
                )
            return {
                "error": result.error
                or {
                    "code": "mcp_tool_failed",
                    "message": "MCP tool failed",
                },
                "tool_name": tool_name,
                "status": result.status,
                "metadata": result.metadata,
            }

        call_mcp_tool.__name__ = f"mcp_{_safe_tool_function_name(tool_name)}"
        call_mcp_tool.__doc__ = (
            "Execute an approved MCP tool. "
            "Pass a JSON object with the arguments required by the tool."
        )
        return function_tool(
            call_mcp_tool,
            name_override=tool_name,
            description_override=f"Execute the approved MCP tool `{tool_name}`.",
            strict_mode=False,
            tool_input_guardrails=[_tool_provenance_guardrail(tool_name)],
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

    def _run_config(self, request: AgentRunRequest) -> RunConfig | None:
        if request.tracing is None:
            return None
        return RunConfig(
            workflow_name=request.tracing.workflow_name,
            trace_id=request.tracing.trace_id,
            group_id=request.tracing.group_id,
            trace_metadata=request.tracing.metadata,
            tracing_disabled=request.tracing.disabled,
            trace_include_sensitive_data=request.tracing.include_sensitive_data,
        )

    def _model_settings(self, settings: dict[str, object]) -> ModelSettings:
        temperature = self._float_setting(settings, "temperature")
        top_p = self._float_setting(settings, "top_p")
        frequency_penalty = self._float_setting(settings, "frequency_penalty")
        presence_penalty = self._float_setting(settings, "presence_penalty")
        max_tokens = self._int_setting(settings, "max_tokens")
        parallel_tool_calls = self._bool_setting(settings, "parallel_tool_calls")
        tool_choice = self._tool_choice_setting(settings)
        store = self._bool_setting(settings, "store")
        include_usage = self._bool_setting(settings, "include_usage")
        truncation = settings.get("truncation")
        safe_truncation: Literal["auto", "disabled"] | None = None
        if truncation == "auto" or truncation == "disabled":
            safe_truncation = truncation
        verbosity = settings.get("verbosity")
        safe_verbosity: Literal["low", "medium", "high"] | None = None
        if verbosity == "low" or verbosity == "medium" or verbosity == "high":
            safe_verbosity = verbosity
        metadata = settings.get("metadata")
        safe_metadata: dict[str, str] | None = None
        if isinstance(metadata, dict) and all(
            isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
        ):
            safe_metadata = {str(key): str(value) for key, value in metadata.items()}
        return ModelSettings(
            temperature=temperature,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            truncation=safe_truncation,
            verbosity=safe_verbosity,
            metadata=safe_metadata,
            store=store,
            include_usage=include_usage,
        )

    def _float_setting(self, settings: dict[str, object], key: str) -> float | None:
        value = settings.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return None

    def _int_setting(self, settings: dict[str, object], key: str) -> int | None:
        value = settings.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    def _bool_setting(self, settings: dict[str, object], key: str) -> bool | None:
        value = settings.get(key)
        if isinstance(value, bool):
            return value
        return None

    def _tool_choice_setting(self, settings: dict[str, object]) -> str | None:
        value = settings.get("tool_choice")
        if not isinstance(value, str) or not value:
            return None
        if value in {"auto", "required", "none"}:
            return value
        return value if value.replace("_", "").replace("-", "").isalnum() else None

    def _safe_raw_output(self, result: Any) -> dict[str, object]:
        payload: dict[str, object] = {"final_output": str(getattr(result, "final_output", ""))}
        sdk_continuation: dict[str, object] = {
            "provider": "openai_agents",
            "mode": "runner_level_fallback",
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
        to_input_list = getattr(result, "to_input_list", None)
        if callable(to_input_list):
            try:
                resume_input = self._jsonable(to_input_list(mode="normalized"))
                payload["resume_input"] = resume_input
                sdk_continuation["resume_input"] = resume_input
            except Exception as exc:  # pragma: no cover - SDK internals are best-effort.
                payload["resume_input_error"] = type(exc).__name__
                sdk_continuation["resume_input_error"] = type(exc).__name__
        to_state = getattr(result, "to_state", None)
        if callable(to_state):
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
        usage = getattr(result, "usage", None)
        if usage is not None:
            payload["usage"] = self._jsonable(usage)
        payload["sdk_continuation"] = sdk_continuation
        return redact_sensitive_payload(payload)

    def _runtime_events(self, result: Any) -> list[AgentRuntimeEvent]:
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
                    payload={"usage": self._jsonable(usage)},
                )
            )
        raw_events = getattr(result, "events", None)
        if isinstance(raw_events, list | tuple):
            for item in raw_events:
                event = self._runtime_event_from_sdk_item(item)
                if event is not None:
                    events.append(event)
        return events

    def _runtime_event_from_sdk_item(self, item: object) -> AgentRuntimeEvent | None:
        event_type = getattr(item, "type", None) or getattr(item, "event_type", None)
        if not isinstance(event_type, str) or not event_type:
            return None
        payload = self._jsonable(item)
        return AgentRuntimeEvent(
            event_type=event_type,
            message=str(getattr(item, "message", "") or event_type),
            payload=redact_sensitive_payload(
                payload if isinstance(payload, dict) else {"value": payload}
            ),
        )

    def _jsonable(self, value: Any) -> object:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, dict):
            return {str(key): self._jsonable(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._jsonable(item) for item in value]
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, dict):
                return self._jsonable(dumped)
        return str(value)


def _safe_tool_function_name(tool_name: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in tool_name)
    return safe or "tool"


def _tool_response_with_metadata(
    *,
    tool_name: str,
    status: str,
    payload: dict[str, object],
    metadata: dict[str, object],
) -> dict[str, object]:
    response = dict(payload)
    response.setdefault("tool_name", tool_name)
    response.setdefault("status", status)
    response["metadata"] = metadata
    return response


def _tool_provenance_guardrail(tool_name: str) -> ToolInputGuardrail[Any]:
    async def assert_runtime_tool_provenance(data: Any) -> ToolGuardrailFunctionOutput:
        runtime_context = getattr(getattr(data, "context", None), "context", None)
        allowed_tools = _runtime_allowed_tools(runtime_context)
        allowed = tool_name in allowed_tools
        return ToolGuardrailFunctionOutput(
            output_info={
                "tool_name": tool_name,
                "guardrail": "runtime_allowed_tool_provenance",
                "allowed": allowed,
            },
            behavior={"type": "allow"}
            if allowed
            else {
                "type": "reject_content",
                "message": f"Tool {tool_name} is not present in runtime context.",
            },
        )

    return ToolInputGuardrail(
        assert_runtime_tool_provenance,
        name=f"{tool_name}:runtime_allowed_tool_provenance",
    )


def _runtime_allowed_tools(runtime_context: object) -> tuple[str, ...]:
    raw_allowed_tools: object
    if isinstance(runtime_context, dict):
        raw_allowed_tools = runtime_context.get("allowed_tools", ())
    else:
        raw_allowed_tools = getattr(runtime_context, "allowed_tools", ())
    if not isinstance(raw_allowed_tools, list | tuple):
        return ()
    return tuple(tool for tool in raw_allowed_tools if isinstance(tool, str))


def _model_provider_circuit_key(request: AgentRunRequest) -> str:
    provider = model_provider_key(request.provider) or "openai"
    base_url = normalize_openai_compatible_base_url(request.base_url) or "openai-default"
    try:
        parsed = urlparse(base_url)
    except ValueError:
        host = "invalid-url"
    else:
        host = parsed.netloc or parsed.path or "openai-default"
    credential = (
        str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else "no-credential"
    )
    model_api = canonical_model_api(request.model_api) or "sdk-default"
    return (
        f"model-provider:{provider}:{host}:{credential}:{model_api}:"
        f"{request.model or request.agent_profile.model}"
    )


def _use_responses_api(model_api: str | None) -> bool | None:
    normalized = canonical_model_api(model_api)
    if normalized == OPENAI_CHAT_COMPLETIONS_API:
        return False
    if normalized == OPENAI_RESPONSES_API:
        return True
    return None
