import json
from urllib.parse import urlparse

import httpx

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeEvent,
    AgentRuntimeToolResult,
    AgentRunTracing,
)
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.core.resilience import CircuitBreakerConfig, async_retry_with_circuit
from backend.app.model_providers.model_api import ANTHROPIC_MESSAGES_API
from backend.app.model_providers.provider_keys import canonical_model_provider
from backend.app.security.redaction import redact_sensitive_payload

ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG = CircuitBreakerConfig()


class AnthropicMessagesRunner:
    def __init__(
        self,
        *,
        max_attempts: int = 1,
        circuit_config: CircuitBreakerConfig = DEFAULT_MODEL_PROVIDER_CIRCUIT_CONFIG,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._circuit_config = circuit_config
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if not request.api_key:
            raise ValueError("Anthropic provider requires an api_key")
        result = await async_retry_with_circuit(
            key=_model_provider_circuit_key(request),
            func=lambda: self._run_once(request),
            max_attempts=self._max_attempts,
            circuit_config=self._circuit_config,
            should_retry=lambda exc: normalize_agent_error(exc).retryable,
        )
        return result

    async def _run_once(self, request: AgentRunRequest) -> AgentRunResult:
        user_input = self._input_for_request(request)
        messages = await self._messages_for_request(request, user_input)
        first_response = await self._create_message(request, messages)
        tool_uses = _tool_uses(first_response)
        if not tool_uses or request.tool_executor is None:
            result = _result_from_response(request, first_response)
            await self._persist_session_turn(request, user_input, result.final_output)
            return result

        tool_results = [
            (tool_use, self._execute_tool_use(request, tool_use))
            for tool_use in tool_uses
        ]
        messages.append(
            {
                "role": "assistant",
                "content": first_response.get("content", []),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    self._tool_result_content(tool_use=tool_use, result=result)
                    for tool_use, result in tool_results
                ],
            }
        )
        final_response = await self._create_message(request, messages)
        events = [
            AgentRuntimeEvent(
                event_type="tool.completed",
                message="Anthropic tool call completed.",
                payload={
                    "tool_name": str(tool_use.get("name")),
                    "tool_call_id": str(tool_use.get("id")),
                    "status": result.status,
                    "metadata": redact_sensitive_payload(dict(result.metadata)),
                },
            )
            for tool_use, result in tool_results
        ]
        result = _result_from_response(request, final_response, events=events)
        await self._persist_session_turn(request, user_input, result.final_output)
        return result

    async def _messages_for_request(
        self,
        request: AgentRunRequest,
        user_input: str,
    ) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        if request.session is not None:
            for item in await request.session.get_items():
                message = _session_item_to_message(item)
                if message is not None:
                    messages.append(message)
        messages.append({"role": "user", "content": user_input})
        return messages

    async def _persist_session_turn(
        self,
        request: AgentRunRequest,
        user_input: str,
        final_output: str,
    ) -> None:
        if request.session is None:
            return
        await request.session.add_items(
            [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": final_output},
            ]
        )

    async def _create_message(
        self,
        request: AgentRunRequest,
        messages: list[dict[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model or request.agent_profile.model,
            "max_tokens": _max_tokens(request.agent_profile.model_settings),
            "system": request.agent_profile.instructions,
            "messages": messages,
        }
        temperature = _float_setting(request.agent_profile.model_settings, "temperature")
        if temperature is not None:
            payload["temperature"] = temperature
        tools = _anthropic_tools(request)
        if tools:
            payload["tools"] = tools
            tool_choice = _anthropic_tool_choice(request.agent_profile.model_settings)
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        client = self._client
        if client is not None:
            response = await client.post(
                _messages_url(request.base_url),
                headers=_headers(request.api_key or ""),
                json=payload,
            )
            response.raise_for_status()
            return _json_object(response.json())
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as ephemeral_client:
            response = await ephemeral_client.post(
                _messages_url(request.base_url),
                headers=_headers(request.api_key or ""),
                json=payload,
            )
            response.raise_for_status()
            return _json_object(response.json())

    def _execute_tool_use(
        self,
        request: AgentRunRequest,
        tool_use: dict[str, object],
    ) -> AgentRuntimeToolResult:
        tool_name = str(tool_use.get("name") or "")
        if tool_name not in request.context.allowed_tools:
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": "tool_not_allowed",
                    "message": f"Tool {tool_name} is not present in runtime context.",
                },
            )
        if request.tool_executor is None:
            return AgentRuntimeToolResult(
                status="failed",
                error={"code": "tool_executor_missing", "message": "Tool executor missing"},
            )
        return request.tool_executor.execute_tool(
            context=request.context,
            tool_name=tool_name,
            arguments=_json_object(tool_use.get("input")),
        )

    def _tool_result_content(
        self,
        *,
        tool_use: dict[str, object],
        result: AgentRuntimeToolResult,
    ) -> dict[str, object]:
        return {
            "type": "tool_result",
            "tool_use_id": str(tool_use.get("id") or ""),
            "is_error": result.status != "completed",
            "content": json.dumps(
                result.output if result.status == "completed" else result.error,
                ensure_ascii=False,
                sort_keys=True,
            ),
        }

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


def _headers(api_key: str) -> dict[str, str]:
    return {
        "anthropic-version": ANTHROPIC_VERSION,
        "x-api-key": api_key,
        "content-type": "application/json",
    }


def _messages_url(base_url: str | None) -> str:
    root = (base_url or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
    if root.endswith("/v1/messages"):
        return root
    if root.endswith("/v1"):
        return f"{root}/messages"
    return f"{root}/v1/messages"


def _anthropic_tools(request: AgentRunRequest) -> list[dict[str, object]]:
    if request.tool_executor is None:
        return []
    return [
        {
            "name": tool_name,
            "description": f"Execute the approved MCP tool `{tool_name}`.",
            "input_schema": {
                "type": "object",
                "additionalProperties": True,
            },
        }
        for tool_name in request.context.allowed_tools
    ]


def _anthropic_tool_choice(settings: dict[str, object] | None) -> dict[str, object] | None:
    if settings is None:
        return None
    value = settings.get("tool_choice")
    if value == "auto":
        return {"type": "auto"}
    if value == "required":
        return {"type": "any"}
    if isinstance(value, str) and value and value != "none":
        return {"type": "tool", "name": value}
    return None


def _tool_uses(response: dict[str, object]) -> list[dict[str, object]]:
    content = response.get("content")
    if not isinstance(content, list):
        return []
    return [
        item
        for item in content
        if isinstance(item, dict) and item.get("type") == "tool_use"
    ]


def _result_from_response(
    request: AgentRunRequest,
    response: dict[str, object],
    *,
    events: list[AgentRuntimeEvent] | None = None,
) -> AgentRunResult:
    text = _response_text(response)
    usage = response.get("usage")
    runtime_events = list(events or [])
    if isinstance(usage, dict):
        runtime_events.append(
            AgentRuntimeEvent(
                event_type="model.usage",
                message="Model usage recorded.",
                payload={"usage": usage},
            )
        )
    runtime_events.append(_model_request_event(request))
    return AgentRunResult(
        final_output=text,
        raw_output=_safe_raw_output(request, response),
        events=tuple(runtime_events),
    )


def _safe_raw_output(
    request: AgentRunRequest,
    response: dict[str, object],
) -> dict[str, object]:
    return {
        "provider": request.provider or "anthropic",
        "model": request.model or request.agent_profile.model,
        "model_api": _anthropic_model_api(request),
        "model_provider_credential_id": str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else None,
        "trace": _trace_payload(request.tracing),
        "response": redact_sensitive_payload(dict(response)),
    }


def _model_request_event(request: AgentRunRequest) -> AgentRuntimeEvent:
    return AgentRuntimeEvent(
        event_type="model.request",
        message="Model provider request metadata recorded.",
        payload={
            "model_provider": {
                "provider": request.provider or "anthropic",
                "model": request.model or request.agent_profile.model,
                "model_api": _anthropic_model_api(request),
                "credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
            },
            "trace": _trace_event_payload(request.tracing),
        },
    )


def _anthropic_model_api(request: AgentRunRequest) -> str:
    return ANTHROPIC_MESSAGES_API


def _trace_event_payload(tracing: AgentRunTracing | None) -> dict[str, object] | None:
    if tracing is None:
        return None
    return {
        "workflow_name": tracing.workflow_name,
        "trace_id": tracing.trace_id,
        "group_id": tracing.group_id,
        "metadata": redact_sensitive_payload(dict(tracing.metadata)),
        "disabled": tracing.disabled,
    }


def _trace_payload(tracing: AgentRunTracing | None) -> dict[str, object] | None:
    if tracing is None:
        return None
    return {
        "workflow_name": tracing.workflow_name,
        "trace_id": tracing.trace_id,
        "group_id": tracing.group_id,
        "metadata": redact_sensitive_payload(dict(tracing.metadata)),
        "disabled": tracing.disabled,
        "include_sensitive_data": tracing.include_sensitive_data,
    }


def _response_text(response: dict[str, object]) -> str:
    content = response.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(item.get("text"))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text") is not None
    )


def _session_item_to_message(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    role = item.get("role")
    content = item.get("content")
    if role not in {"user", "assistant"}:
        return None
    if not isinstance(content, str) or not content:
        return None
    return {"role": role, "content": content}


def _max_tokens(settings: dict[str, object] | None) -> int:
    if settings is None:
        return 1024
    value = settings.get("max_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) else 1024


def _float_setting(settings: dict[str, object] | None, key: str) -> float | None:
    if settings is None:
        return None
    value = settings.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _json_object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _model_provider_circuit_key(request: AgentRunRequest) -> str:
    provider = canonical_model_provider(request.provider or "anthropic")
    host = _base_url_host(request.base_url) or "anthropic-default"
    model_api = _anthropic_model_api(request)
    credential = (
        str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else "no-credential"
    )
    return (
        f"model-provider:{provider}:{host}:{credential}:{model_api}:"
        f"{request.model or request.agent_profile.model}"
    )


def _base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    return parsed.netloc or parsed.path or None
