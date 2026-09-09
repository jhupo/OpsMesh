from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from anthropic import Anthropic, AnthropicError
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.model_providers.model_api import (
    ANTHROPIC_MESSAGES_API,
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
)
from backend.app.model_providers.provider_keys import (
    canonical_model_provider,
    is_anthropic_provider,
    is_openai_compatible_provider,
)
from backend.app.model_providers.service_models import ResolvedModelProvider
from backend.app.security.redaction import redact_sensitive_payload

OPENAI_RESPONSES_REVIEWER = "openai_responses"
OPENAI_CHAT_REVIEWER = "openai_chat_completions"
ANTHROPIC_MESSAGES_REVIEWER = "anthropic_messages"
_MAX_REVIEW_OUTPUT_TOKENS = 1_200


@dataclass(frozen=True)
class LlmReviewResult:
    required: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]


class ResourceReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    severity: Literal["low", "medium", "high", "critical"]
    category: Literal["security", "privacy", "execution", "permissions", "quality", "operations"]
    message: str = Field(min_length=1, max_length=500)


class StructuredResourceReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: Literal["approve", "needs_admin_review", "reject"]
    risk_level: Literal["low", "medium", "high", "critical"]
    reasons: list[str] = Field(min_length=1, max_length=10)
    findings: list[ResourceReviewFinding] = Field(max_length=10)
    recommendation: str = Field(min_length=1, max_length=1_000)


@dataclass(frozen=True)
class ResourceReviewAdapterRequest:
    provider: ResolvedModelProvider
    input_text: str
    timeout_seconds: float


class ResourceReviewProviderAdapter(ABC):
    @property
    @abstractmethod
    def key(self) -> str: ...

    @abstractmethod
    def review(self, request: ResourceReviewAdapterRequest) -> StructuredResourceReview: ...


class OpenAIResponsesResourceReviewAdapter(ResourceReviewProviderAdapter):
    def __init__(self, client_factory: Callable[..., OpenAI] = OpenAI) -> None:
        self._client_factory = client_factory

    @property
    def key(self) -> str:
        return OPENAI_RESPONSES_REVIEWER

    def review(self, request: ResourceReviewAdapterRequest) -> StructuredResourceReview:
        with self._client_factory(
            api_key=request.provider.api_key,
            base_url=request.provider.base_url,
            max_retries=0,
        ) as client:
            response = client.responses.parse(
                model=request.provider.model,
                instructions=_SYSTEM_PROMPT,
                input=request.input_text,
                text_format=StructuredResourceReview,
                max_output_tokens=_MAX_REVIEW_OUTPUT_TOKENS,
                store=False,
                timeout=request.timeout_seconds,
            )
        if response.output_parsed is None:
            raise ValueError("OpenAI Responses review returned no structured output")
        return response.output_parsed


class OpenAIChatCompletionsResourceReviewAdapter(ResourceReviewProviderAdapter):
    def __init__(self, client_factory: Callable[..., OpenAI] = OpenAI) -> None:
        self._client_factory = client_factory

    @property
    def key(self) -> str:
        return OPENAI_CHAT_REVIEWER

    def review(self, request: ResourceReviewAdapterRequest) -> StructuredResourceReview:
        with self._client_factory(
            api_key=request.provider.api_key,
            base_url=request.provider.base_url,
            max_retries=0,
        ) as client:
            completion = client.chat.completions.parse(
                model=request.provider.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": request.input_text},
                ],
                response_format=StructuredResourceReview,
                timeout=request.timeout_seconds,
            )
        if not completion.choices or completion.choices[0].message.parsed is None:
            raise ValueError("OpenAI Chat Completions review returned no structured output")
        return completion.choices[0].message.parsed


class AnthropicMessagesResourceReviewAdapter(ResourceReviewProviderAdapter):
    def __init__(self, client_factory: Callable[..., Anthropic] = Anthropic) -> None:
        self._client_factory = client_factory

    @property
    def key(self) -> str:
        return ANTHROPIC_MESSAGES_REVIEWER

    def review(self, request: ResourceReviewAdapterRequest) -> StructuredResourceReview:
        with self._client_factory(
            api_key=request.provider.api_key,
            base_url=request.provider.base_url,
            max_retries=0,
        ) as client:
            message = client.messages.parse(
                model=request.provider.model,
                max_tokens=_MAX_REVIEW_OUTPUT_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": request.input_text}],
                output_format=StructuredResourceReview,
                timeout=request.timeout_seconds,
            )
        if message.parsed_output is None:
            raise ValueError("Anthropic Messages review returned no structured output")
        return message.parsed_output


class LlmResourceReviewer:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        adapters: Mapping[str, ResourceReviewProviderAdapter] | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._adapters = dict(_default_adapters() if adapters is None else adapters)

    def review(
        self,
        *,
        provider: ResolvedModelProvider,
        resource_type: str,
        resource: dict[str, object],
        static_signals: dict[str, object],
        timeout_seconds: float | None = None,
    ) -> LlmReviewResult:
        if not provider.api_key:
            raise ValueError("Review model provider is missing api_key")
        adapter_key = _reviewer_key(provider)
        adapter = self._adapters.get(adapter_key)
        if adapter is None:
            raise ValueError(f"Review adapter is not configured for {adapter_key}")
        request = ResourceReviewAdapterRequest(
            provider=provider,
            input_text=_review_input(resource_type, resource, static_signals),
            timeout_seconds=timeout_seconds or self._timeout_seconds,
        )
        try:
            decision = adapter.review(request)
        except (OpenAIError, AnthropicError, ValidationError, ValueError, OSError) as exc:
            raise RuntimeError("Review model request failed") from exc
        return _review_result(decision, provider=provider, adapter_key=adapter.key)


def _default_adapters() -> dict[str, ResourceReviewProviderAdapter]:
    adapters: tuple[ResourceReviewProviderAdapter, ...] = (
        OpenAIResponsesResourceReviewAdapter(),
        OpenAIChatCompletionsResourceReviewAdapter(),
        AnthropicMessagesResourceReviewAdapter(),
    )
    return {adapter.key: adapter for adapter in adapters}


def _reviewer_key(provider: ResolvedModelProvider) -> str:
    provider_name = canonical_model_provider(provider.provider)
    if is_anthropic_provider(provider_name):
        if provider.model_api not in {None, ANTHROPIC_MESSAGES_API}:
            raise ValueError(f"Unsupported Anthropic review model API: {provider.model_api}")
        return ANTHROPIC_MESSAGES_REVIEWER
    if not is_openai_compatible_provider(provider_name):
        raise ValueError(f"Unsupported review model provider: {provider_name or 'unset'}")
    if provider.model_api == OPENAI_CHAT_COMPLETIONS_API:
        return OPENAI_CHAT_REVIEWER
    if provider.model_api == OPENAI_RESPONSES_API:
        return OPENAI_RESPONSES_REVIEWER
    if provider.model_api is None:
        return OPENAI_RESPONSES_REVIEWER if provider_name == "openai" else OPENAI_CHAT_REVIEWER
    raise ValueError(f"Unsupported OpenAI review model API: {provider.model_api}")


def _review_input(
    resource_type: str,
    resource: dict[str, object],
    static_signals: dict[str, object],
) -> str:
    return json.dumps(
        {
            "resource_type": resource_type,
            "resource": redact_sensitive_payload(resource),
            "static_signals": redact_sensitive_payload(static_signals),
        },
        ensure_ascii=True,
        sort_keys=True,
    )


def _review_result(
    decision: StructuredResourceReview,
    *,
    provider: ResolvedModelProvider,
    adapter_key: str,
) -> LlmReviewResult:
    required = decision.verdict != "approve" or decision.risk_level in {"high", "critical"}
    reasons = [reason[:160] for reason in decision.reasons if reason]
    return LlmReviewResult(
        required=required,
        risk_level=decision.risk_level,
        reasons=reasons
        or ["llm_review.requires_admin_review" if required else "llm_review.approved"],
        signals={
            "reviewer": "provider_sdk",
            "provider": canonical_model_provider(provider.provider),
            "adapter": adapter_key,
            "verdict": decision.verdict,
            "findings": [finding.model_dump(mode="json") for finding in decision.findings],
            "recommendation": decision.recommendation,
        },
    )


_SYSTEM_PROMPT = """
You are a senior security and product reviewer for a multi-agent workspace platform.
Review newly created employees, skills, MCP servers, and MCP tool permissions before activation.

Return a structured decision matching the supplied output schema.
Require admin review for dangerous execution, broad filesystem/network access,
deletion/destructive tools, credential exfiltration risk, approval bypass,
production deployment control, or unclear high-impact authority.
Approve normal scoped tools, authenticated remote MCP servers, and runtime tool
permissions that already require per-run approval, unless the resource grants
broad dangerous capability.
Do not reject unless the resource is clearly malicious or impossible to govern safely.
Use short machine-readable reason identifiers and concise findings.
""".strip()


__all__ = [
    "ANTHROPIC_MESSAGES_REVIEWER",
    "OPENAI_CHAT_REVIEWER",
    "OPENAI_RESPONSES_REVIEWER",
    "AnthropicMessagesResourceReviewAdapter",
    "LlmResourceReviewer",
    "LlmReviewResult",
    "OpenAIChatCompletionsResourceReviewAdapter",
    "OpenAIResponsesResourceReviewAdapter",
    "ResourceReviewAdapterRequest",
    "ResourceReviewProviderAdapter",
    "StructuredResourceReview",
]
