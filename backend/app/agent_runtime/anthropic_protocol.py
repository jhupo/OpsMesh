from __future__ import annotations

from urllib.parse import urlparse

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeEvent,
    AgentRunTracing,
)
from backend.app.model_providers.model_api import ANTHROPIC_MESSAGES_API
from backend.app.model_providers.provider_keys import canonical_model_provider
from backend.app.security.redaction import redact_sensitive_payload

ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


def headers(api_key: str) -> dict[str, str]:
    return {
        "anthropic-version": ANTHROPIC_VERSION,
        "x-api-key": api_key,
        "content-type": "application/json",
    }


def messages_url(base_url: str | None) -> str:
    root = (base_url or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
    if root.endswith("/v1/messages"):
        return root
    if root.endswith("/v1"):
        return f"{root}/messages"
    return f"{root}/v1/messages"


def tool_definitions(request: AgentRunRequest) -> list[dict[str, object]]:
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


def tool_choice(settings: dict[str, object] | None) -> dict[str, object] | None:
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


def tool_uses(response: dict[str, object]) -> list[dict[str, object]]:
    content = response.get("content")
    if not isinstance(content, list):
        return []
    return [
        item
        for item in content
        if isinstance(item, dict) and item.get("type") == "tool_use"
    ]


def result_from_response(
    request: AgentRunRequest,
    response: dict[str, object],
    *,
    events: list[AgentRuntimeEvent] | None = None,
) -> AgentRunResult:
    text = response_text(response)
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
    runtime_events.append(model_request_event(request))
    return AgentRunResult(
        final_output=text,
        raw_output=safe_raw_output(request, response),
        events=tuple(runtime_events),
    )


def safe_raw_output(
    request: AgentRunRequest,
    response: dict[str, object],
) -> dict[str, object]:
    return {
        "provider": request.provider or "anthropic",
        "model": request.model or request.agent_profile.model,
        "model_api": anthropic_model_api(request),
        "model_provider_credential_id": str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else None,
        "trace": trace_payload(request.tracing),
        "response": redact_sensitive_payload(dict(response)),
    }


def model_request_event(request: AgentRunRequest) -> AgentRuntimeEvent:
    return AgentRuntimeEvent(
        event_type="model.request",
        message="Model provider request metadata recorded.",
        payload={
            "model_provider": {
                "provider": request.provider or "anthropic",
                "model": request.model or request.agent_profile.model,
                "model_api": anthropic_model_api(request),
                "credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
            },
            "trace": trace_event_payload(request.tracing),
        },
    )


def anthropic_model_api(request: AgentRunRequest) -> str:
    return ANTHROPIC_MESSAGES_API


def trace_event_payload(tracing: AgentRunTracing | None) -> dict[str, object] | None:
    if tracing is None:
        return None
    return {
        "workflow_name": tracing.workflow_name,
        "trace_id": tracing.trace_id,
        "group_id": tracing.group_id,
        "metadata": redact_sensitive_payload(dict(tracing.metadata)),
        "disabled": tracing.disabled,
    }


def trace_payload(tracing: AgentRunTracing | None) -> dict[str, object] | None:
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


def response_text(response: dict[str, object]) -> str:
    content = response.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(item.get("text"))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text") is not None
    )


def session_item_to_message(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    role = item.get("role")
    content = item.get("content")
    if role not in {"user", "assistant"}:
        return None
    if not isinstance(content, str) or not content:
        return None
    return {"role": role, "content": content}


def max_tokens(settings: dict[str, object] | None) -> int:
    if settings is None:
        return 1024
    value = settings.get("max_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) else 1024


def float_setting(settings: dict[str, object] | None, key: str) -> float | None:
    if settings is None:
        return None
    value = settings.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def json_object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def model_provider_circuit_key(request: AgentRunRequest) -> str:
    provider = canonical_model_provider(request.provider or "anthropic")
    host = base_url_host(request.base_url) or "anthropic-default"
    model_api = anthropic_model_api(request)
    credential = (
        str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else "no-credential"
    )
    return (
        f"model-provider:{provider}:{host}:{credential}:{model_api}:"
        f"{request.model or request.agent_profile.model}"
    )


def base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    return parsed.netloc or parsed.path or None
