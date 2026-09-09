from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.model_providers.model_api import (
    ANTHROPIC_MESSAGES_API,
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
)
from backend.app.model_providers.service_models import ResolvedModelProvider
from backend.app.reviews.llm import (
    ANTHROPIC_MESSAGES_REVIEWER,
    OPENAI_CHAT_REVIEWER,
    OPENAI_RESPONSES_REVIEWER,
    AnthropicMessagesResourceReviewAdapter,
    LlmResourceReviewer,
    OpenAIChatCompletionsResourceReviewAdapter,
    OpenAIResponsesResourceReviewAdapter,
    ResourceReviewFinding,
    StructuredResourceReview,
)


def test_openai_responses_reviewer_uses_sdk_structured_output() -> None:
    decision = _decision()
    client = _FakeOpenAIClient(responses_output=decision)
    factory = _RecordingFactory(client)
    reviewer = LlmResourceReviewer(
        adapters={
            OPENAI_RESPONSES_REVIEWER: OpenAIResponsesResourceReviewAdapter(factory),
        }
    )

    result = reviewer.review(
        provider=_provider(provider="openai", model_api=OPENAI_RESPONSES_API),
        resource_type="skill",
        resource={"name": "Research", "api_key": "sk-secret"},
        static_signals={"risk": "low"},
        timeout_seconds=7,
    )

    call = client.responses.calls[0]
    assert call["text_format"] is StructuredResourceReview
    assert call["timeout"] == 7
    assert "sk-secret" not in str(call["input"])
    assert factory.calls == [{"api_key": "provider-secret", "base_url": None, "max_retries": 0}]
    assert result.required is False
    assert result.signals["adapter"] == OPENAI_RESPONSES_REVIEWER


def test_openai_compatible_default_reviewer_uses_chat_completions_sdk() -> None:
    client = _FakeOpenAIClient(chat_output=_decision(risk_level="high"))
    reviewer = LlmResourceReviewer(
        adapters={
            OPENAI_CHAT_REVIEWER: OpenAIChatCompletionsResourceReviewAdapter(
                _RecordingFactory(client)
            ),
        }
    )

    result = reviewer.review(
        provider=_provider(provider="openai-compatible", model_api=None),
        resource_type="mcp_server",
        resource={"server_type": "streamable_http"},
        static_signals={},
    )

    call = client.chat.completions.calls[0]
    assert call["response_format"] is StructuredResourceReview
    assert call["model"] == "review-model"
    assert result.required is True
    assert result.risk_level == "high"


def test_explicit_openai_chat_reviewer_uses_configured_protocol() -> None:
    client = _FakeOpenAIClient(chat_output=_decision())
    reviewer = LlmResourceReviewer(
        adapters={
            OPENAI_CHAT_REVIEWER: OpenAIChatCompletionsResourceReviewAdapter(
                _RecordingFactory(client)
            ),
        }
    )

    reviewer.review(
        provider=_provider(provider="openai", model_api=OPENAI_CHAT_COMPLETIONS_API),
        resource_type="agent_profile",
        resource={"name": "Planner"},
        static_signals={},
    )

    assert len(client.chat.completions.calls) == 1
    assert client.responses.calls == []


def test_anthropic_reviewer_uses_messages_parse_structured_output() -> None:
    client = _FakeAnthropicClient(_decision(verdict="needs_admin_review"))
    factory = _RecordingFactory(client)
    reviewer = LlmResourceReviewer(
        adapters={
            ANTHROPIC_MESSAGES_REVIEWER: AnthropicMessagesResourceReviewAdapter(factory),
        }
    )

    result = reviewer.review(
        provider=_provider(provider="anthropic", model_api=ANTHROPIC_MESSAGES_API),
        resource_type="mcp_tool_allowlist",
        resource={"tool_name": "deploy"},
        static_signals={"execution": "write"},
        timeout_seconds=9,
    )

    call = client.messages.calls[0]
    assert call["output_format"] is StructuredResourceReview
    assert call["timeout"] == 9
    assert call["max_tokens"] == 1_200
    assert result.required is True
    assert result.signals["provider"] == "anthropic"


def test_reviewer_fails_closed_when_sdk_returns_no_structured_output() -> None:
    client = _FakeOpenAIClient(responses_output=None)
    reviewer = LlmResourceReviewer(
        adapters={
            OPENAI_RESPONSES_REVIEWER: OpenAIResponsesResourceReviewAdapter(
                _RecordingFactory(client)
            ),
        }
    )

    with pytest.raises(RuntimeError, match="Review model request failed"):
        reviewer.review(
            provider=_provider(provider="openai", model_api=OPENAI_RESPONSES_API),
            resource_type="skill",
            resource={"name": "Research"},
            static_signals={},
        )


def test_reviewer_rejects_unsupported_provider_without_request() -> None:
    reviewer = LlmResourceReviewer(adapters={})

    with pytest.raises(ValueError, match="Unsupported review model provider"):
        reviewer.review(
            provider=_provider(provider="unknown", model_api=None),
            resource_type="skill",
            resource={},
            static_signals={},
        )


def _decision(
    *,
    verdict: str = "approve",
    risk_level: str = "low",
) -> StructuredResourceReview:
    return StructuredResourceReview.model_validate(
        {
            "verdict": verdict,
            "risk_level": risk_level,
            "reasons": ["resource.scoped"],
            "findings": [
                ResourceReviewFinding(
                    severity="low",
                    category="permissions",
                    message="The resource is scoped.",
                )
            ],
            "recommendation": "Approve with normal audit logging.",
        }
    )


def _provider(*, provider: str, model_api: str | None) -> ResolvedModelProvider:
    return ResolvedModelProvider(
        provider=provider,
        model="review-model",
        base_url=None,
        api_key="provider-secret",
        model_api=model_api,
        credential_id=None,
    )


class _RecordingFactory:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> Any:
        self.calls.append(dict(kwargs))
        return self._client


class _FakeOpenAIResource:
    def __init__(self, output: StructuredResourceReview | None) -> None:
        self._output = output
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(output_parsed=self._output)


class _FakeChatCompletionsResource:
    def __init__(self, output: StructuredResourceReview | None) -> None:
        self._output = output
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=self._output))]
        )


class _FakeOpenAIClient:
    def __init__(
        self,
        *,
        responses_output: StructuredResourceReview | None = None,
        chat_output: StructuredResourceReview | None = None,
    ) -> None:
        self.responses = _FakeOpenAIResource(responses_output)
        self.chat = SimpleNamespace(completions=_FakeChatCompletionsResource(chat_output))

    def __enter__(self) -> _FakeOpenAIClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeAnthropicMessagesResource:
    def __init__(self, output: StructuredResourceReview | None) -> None:
        self._output = output
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(parsed_output=self._output)


class _FakeAnthropicClient:
    def __init__(self, output: StructuredResourceReview | None) -> None:
        self.messages = _FakeAnthropicMessagesResource(output)

    def __enter__(self) -> _FakeAnthropicClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None
