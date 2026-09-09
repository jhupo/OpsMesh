from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from backend.app.model_providers.model_api import (
    ANTHROPIC_MESSAGES_API,
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
    canonical_model_api,
)
from backend.app.model_providers.provider_keys import canonical_model_provider

ProviderHealthStatus = Literal["healthy", "degraded", "unhealthy"]
ProviderProbeName = Literal["models", "inference"]
ProbeOperation = Callable[[], Awaitable[dict[str, object]]]
OpenAIClientFactory = Callable[..., AsyncOpenAI]
AnthropicClientFactory = Callable[..., AsyncAnthropic]

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
    adapter = ProviderHealthRegistry().resolve(target.provider)
    checks: tuple[ModelProviderHealthCheck, ...]
    if adapter is None:
        checks = (
            ModelProviderHealthCheck(
                name="models",
                status="failed",
                code="unsupported_provider",
                message=f"Provider '{target.provider}' is not supported by health checks.",
            ),
        )
    else:
        checks = await adapter.probe(
            target,
            probes=probes,
            timeout_seconds=timeout_seconds,
        )
    return ModelProviderHealthCheckResult(
        status=_aggregate_status(checks),
        checks=tuple(checks),
    )


class ProviderHealthProbe(ABC):
    @abstractmethod
    async def probe(
        self,
        target: ModelProviderHealthTarget,
        *,
        probes: tuple[ProviderProbeName, ...],
        timeout_seconds: float,
    ) -> tuple[ModelProviderHealthCheck, ...]: ...


class OpenAICompatibleHealthProbe(ProviderHealthProbe):
    def __init__(self, client_factory: OpenAIClientFactory | None = None) -> None:
        self._client_factory = client_factory or AsyncOpenAI

    async def probe(
        self,
        target: ModelProviderHealthTarget,
        *,
        probes: tuple[ProviderProbeName, ...],
        timeout_seconds: float,
    ) -> tuple[ModelProviderHealthCheck, ...]:
        client = self._client_factory(
            api_key=target.api_key,
            base_url=target.base_url or DEFAULT_OPENAI_BASE_URL,
            max_retries=0,
            timeout=timeout_seconds,
        )
        async with client:
            checks: list[ModelProviderHealthCheck] = []
            if "models" in probes:
                checks.append(
                    await _run_sdk_probe(
                        "models",
                        lambda: self._models_metadata(client, target.model),
                    )
                )
            if "inference" in probes:
                checks.append(
                    await _run_sdk_probe(
                        "inference",
                        lambda: self._inference_metadata(client, target),
                    )
                )
        return tuple(checks)

    async def _models_metadata(
        self,
        client: AsyncOpenAI,
        model: str,
    ) -> dict[str, object]:
        models = await client.models.list()
        return _models_metadata(models.data, model)

    async def _inference_metadata(
        self,
        client: AsyncOpenAI,
        target: ModelProviderHealthTarget,
    ) -> dict[str, object]:
        model_api = canonical_model_api(target.model_api)
        if model_api == OPENAI_RESPONSES_API:
            await client.responses.create(
                model=target.model,
                input="Reply with exactly: ok",
                max_output_tokens=20,
                store=False,
            )
        else:
            await client.chat.completions.create(
                model=target.model,
                messages=[{"role": "user", "content": "Reply with exactly: ok"}],
                temperature=0,
                max_tokens=20,
            )
            model_api = OPENAI_CHAT_COMPLETIONS_API
        return {"model": target.model, "model_api": model_api}


class AnthropicHealthProbe(ProviderHealthProbe):
    def __init__(self, client_factory: AnthropicClientFactory | None = None) -> None:
        self._client_factory = client_factory or AsyncAnthropic

    async def probe(
        self,
        target: ModelProviderHealthTarget,
        *,
        probes: tuple[ProviderProbeName, ...],
        timeout_seconds: float,
    ) -> tuple[ModelProviderHealthCheck, ...]:
        client = self._client_factory(
            api_key=target.api_key,
            base_url=target.base_url or DEFAULT_ANTHROPIC_BASE_URL,
            max_retries=0,
            timeout=timeout_seconds,
        )
        async with client:
            checks: list[ModelProviderHealthCheck] = []
            if "models" in probes:
                checks.append(
                    await _run_sdk_probe(
                        "models",
                        lambda: self._models_metadata(client, target.model),
                    )
                )
            if "inference" in probes:
                checks.append(
                    await _run_sdk_probe(
                        "inference",
                        lambda: self._inference_metadata(client, target),
                    )
                )
        return tuple(checks)

    async def _models_metadata(
        self,
        client: AsyncAnthropic,
        model: str,
    ) -> dict[str, object]:
        models = await client.models.list()
        return _models_metadata(models.data, model)

    async def _inference_metadata(
        self,
        client: AsyncAnthropic,
        target: ModelProviderHealthTarget,
    ) -> dict[str, object]:
        await client.messages.create(
            model=target.model,
            max_tokens=20,
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
        )
        return {"model": target.model, "model_api": ANTHROPIC_MESSAGES_API}


class ProviderHealthRegistry:
    def __init__(self, adapters: dict[str, ProviderHealthProbe] | None = None) -> None:
        self._adapters = (
            dict(adapters)
            if adapters is not None
            else {
                "openai": OpenAICompatibleHealthProbe(),
                "openai-compatible": OpenAICompatibleHealthProbe(),
                "anthropic": AnthropicHealthProbe(),
            }
        )

    def resolve(self, provider: str | None) -> ProviderHealthProbe | None:
        return self._adapters.get(canonical_model_provider(provider))


async def _run_sdk_probe(
    name: ProviderProbeName,
    operation: ProbeOperation,
) -> ModelProviderHealthCheck:
    try:
        metadata = await operation()
    except (openai.APITimeoutError, anthropic.APITimeoutError):
        return ModelProviderHealthCheck(
            name=name,
            status="failed",
            code="timeout",
            message="Provider health check timed out.",
        )
    except (openai.APIStatusError, anthropic.APIStatusError) as exc:
        return _status_failure(name, exc.status_code, str(exc)[:500])
    except (openai.APIConnectionError, anthropic.APIConnectionError) as exc:
        return ModelProviderHealthCheck(
            name=name,
            status="failed",
            code="connection_error",
            message=str(exc)[:500],
        )
    except (openai.OpenAIError, anthropic.AnthropicError) as exc:
        return ModelProviderHealthCheck(
            name=name,
            status="failed",
            code="provider_sdk_error",
            message=str(exc)[:500],
        )
    return ModelProviderHealthCheck(name=name, status="passed", metadata=metadata)


def _status_failure(
    name: ProviderProbeName,
    status_code: int,
    message: str,
) -> ModelProviderHealthCheck:
    if status_code in {401, 403}:
        code = "permission_denied"
    elif status_code == 429:
        code = "rate_limited"
    elif status_code >= 500:
        code = "upstream_error"
    else:
        code = "request_failed"
    return ModelProviderHealthCheck(
        name=name,
        status="failed",
        code=code,
        message=message,
        metadata={"status_code": status_code},
    )


def _aggregate_status(checks: tuple[ModelProviderHealthCheck, ...]) -> ProviderHealthStatus:
    failed = [check for check in checks if check.status == "failed"]
    if not failed:
        return "healthy"
    passed = [check for check in checks if check.status == "passed"]
    if passed:
        return "degraded"
    return "unhealthy"


def _models_metadata(models: Iterable[object], model: str) -> dict[str, object]:
    model_ids = [
        model_id
        for item in models
        if isinstance((model_id := getattr(item, "id", None)), str)
    ]
    return {
        "model_count": len(model_ids),
        "model_present": model in model_ids if model_ids else None,
    }
