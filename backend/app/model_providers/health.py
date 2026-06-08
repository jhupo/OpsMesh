from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urljoin

import httpx

from backend.app.model_providers.model_api import (
    ANTHROPIC_MESSAGES_API,
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
    canonical_model_api,
)
from backend.app.model_providers.provider_keys import (
    is_anthropic_provider,
    is_openai_compatible_provider,
)

ProviderHealthStatus = Literal["healthy", "degraded", "unhealthy"]
ProviderProbeName = Literal["models", "inference"]

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"


@dataclass(frozen=True)
class ModelProviderHealthTarget:
    provider: str | None
    model: str
    api_key: str
    base_url: str | None
    model_api: str | None = None


@dataclass(frozen=True)
class ModelProviderHealthCheck:
    name: ProviderProbeName
    status: Literal["passed", "failed", "skipped"]
    code: str | None = None
    message: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ModelProviderHealthCheckResult:
    status: ProviderHealthStatus
    checks: tuple[ModelProviderHealthCheck, ...]

    @property
    def failure_code(self) -> str | None:
        for check in self.checks:
            if check.status == "failed" and check.code:
                return check.code
        return None

    @property
    def failure_message(self) -> str | None:
        for check in self.checks:
            if check.status == "failed" and check.message:
                return check.message
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [check.as_dict() for check in self.checks],
        }


async def probe_model_provider(
    target: ModelProviderHealthTarget,
    *,
    probes: tuple[ProviderProbeName, ...] = ("models", "inference"),
    timeout_seconds: float = 15,
) -> ModelProviderHealthCheckResult:
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if is_openai_compatible_provider(target.provider):
            checks = await _probe_openai_compatible(client, target, probes=probes)
        elif is_anthropic_provider(target.provider):
            checks = await _probe_anthropic(client, target, probes=probes)
        else:
            checks = (
                ModelProviderHealthCheck(
                    name="models",
                    status="failed",
                    code="unsupported_provider",
                    message=f"Provider '{target.provider}' is not supported by health checks.",
                ),
            )
    return ModelProviderHealthCheckResult(
        status=_aggregate_status(checks),
        checks=tuple(checks),
    )


async def _probe_openai_compatible(
    client: httpx.AsyncClient,
    target: ModelProviderHealthTarget,
    *,
    probes: tuple[ProviderProbeName, ...],
) -> tuple[ModelProviderHealthCheck, ...]:
    checks: list[ModelProviderHealthCheck] = []
    base_url = _base_url(target.base_url, default=DEFAULT_OPENAI_BASE_URL)
    headers = {"Authorization": f"Bearer {target.api_key}"}
    if "models" in probes:
        checks.append(
            await _request_check(
                client,
                name="models",
                method="GET",
                url=_join_url(base_url, "models"),
                headers=headers,
                success_metadata=lambda data: _models_metadata(data, target.model),
            )
        )
    if "inference" in probes:
        checks.append(
            await _openai_inference_check(
                client,
                target=target,
                base_url=base_url,
                headers=headers,
            )
        )
    return tuple(checks)


async def _openai_inference_check(
    client: httpx.AsyncClient,
    *,
    target: ModelProviderHealthTarget,
    base_url: str,
    headers: dict[str, str],
) -> ModelProviderHealthCheck:
    model_api = canonical_model_api(target.model_api)
    if model_api == OPENAI_RESPONSES_API:
        return await _request_check(
            client,
            name="inference",
            method="POST",
            url=_join_url(base_url, "responses"),
            headers=headers,
            json={
                "model": target.model,
                "input": "Reply with exactly: ok",
                "max_output_tokens": 20,
            },
            success_metadata=lambda _data: {
                "model": target.model,
                "model_api": OPENAI_RESPONSES_API,
            },
        )
    return await _request_check(
        client,
        name="inference",
        method="POST",
        url=_join_url(base_url, "chat/completions"),
        headers=headers,
        json={
            "model": target.model,
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
            "temperature": 0,
            "max_tokens": 20,
        },
        success_metadata=lambda _data: {
            "model": target.model,
            "model_api": OPENAI_CHAT_COMPLETIONS_API,
        },
    )


async def _probe_anthropic(
    client: httpx.AsyncClient,
    target: ModelProviderHealthTarget,
    *,
    probes: tuple[ProviderProbeName, ...],
) -> tuple[ModelProviderHealthCheck, ...]:
    checks: list[ModelProviderHealthCheck] = []
    base_url = _base_url(target.base_url, default=DEFAULT_ANTHROPIC_BASE_URL)
    headers = {
        "x-api-key": target.api_key,
        "anthropic-version": "2023-06-01",
    }
    if "models" in probes:
        checks.append(
            await _request_check(
                client,
                name="models",
                method="GET",
                url=_join_url(base_url, "v1/models"),
                headers=headers,
                success_metadata=lambda data: _models_metadata(data, target.model),
            )
        )
    if "inference" in probes:
        checks.append(
            await _request_check(
                client,
                name="inference",
                method="POST",
                url=_join_url(base_url, "v1/messages"),
                headers=headers,
                json={
                    "model": target.model,
                    "max_tokens": 20,
                    "messages": [
                        {"role": "user", "content": "Reply with exactly: ok"}
                    ],
                },
                success_metadata=lambda _data: {
                    "model": target.model,
                    "model_api": ANTHROPIC_MESSAGES_API,
                },
            )
        )
    return tuple(checks)


async def _request_check(
    client: httpx.AsyncClient,
    *,
    name: ProviderProbeName,
    method: str,
    url: str,
    headers: dict[str, str],
    json: dict[str, object] | None = None,
    success_metadata,
) -> ModelProviderHealthCheck:
    try:
        response = await client.request(method, url, headers=headers, json=json)
    except httpx.TimeoutException:
        return ModelProviderHealthCheck(
            name=name,
            status="failed",
            code="timeout",
            message="Provider health check timed out.",
        )
    except httpx.HTTPError as exc:
        return ModelProviderHealthCheck(
            name=name,
            status="failed",
            code=exc.__class__.__name__,
            message=str(exc)[:500],
        )
    if 200 <= response.status_code < 300:
        data = _response_json(response)
        return ModelProviderHealthCheck(
            name=name,
            status="passed",
            metadata=success_metadata(data),
        )
    code, message = _failure_from_response(response)
    return ModelProviderHealthCheck(
        name=name,
        status="failed",
        code=code,
        message=message,
        metadata={"status_code": response.status_code},
    )


def _aggregate_status(checks: tuple[ModelProviderHealthCheck, ...]) -> ProviderHealthStatus:
    failed = [check for check in checks if check.status == "failed"]
    if not failed:
        return "healthy"
    passed = [check for check in checks if check.status == "passed"]
    if passed:
        return "degraded"
    return "unhealthy"


def _models_metadata(data: object, model: str) -> dict[str, object]:
    model_ids = []
    if isinstance(data, dict):
        values = data.get("data")
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    model_ids.append(item["id"])
    return {
        "model_count": len(model_ids),
        "model_present": model in model_ids if model_ids else None,
    }


def _failure_from_response(response: httpx.Response) -> tuple[str, str]:
    text = _error_message(response)
    lowered = text.lower()
    if response.status_code in {401, 403} or "blocked" in lowered:
        return "permission_denied", text
    if response.status_code == 429:
        return "rate_limited", text
    if response.status_code >= 500 or "upstream" in lowered:
        return "upstream_error", text
    return "request_failed", text


def _error_message(response: httpx.Response) -> str:
    data = _response_json(response)
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message[:500]
        message = data.get("message")
        if isinstance(message, str) and message:
            return message[:500]
    return response.text[:500] or response.reason_phrase


def _response_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _base_url(value: str | None, *, default: str) -> str:
    return (value or default).rstrip("/")


def _join_url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url}/", path)
