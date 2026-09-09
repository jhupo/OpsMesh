from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.orm import Session

from backend.app.model_providers.health import (
    AnthropicHealthProbe,
    ModelProviderHealthTarget,
    OpenAICompatibleHealthProbe,
    ProviderHealthRegistry,
)
from backend.app.model_providers.model_api import (
    OPENAI_CHAT_COMPLETIONS_API,
    OPENAI_RESPONSES_API,
)
from backend.app.runtime_manager.backends import build_runtime_backend_registry


def test_health_registry_selects_provider_sdk_adapters() -> None:
    registry = ProviderHealthRegistry()
    assert isinstance(registry.resolve("OpenAI"), OpenAICompatibleHealthProbe)
    assert isinstance(registry.resolve("anthropic"), AnthropicHealthProbe)
    assert registry.resolve("unknown") is None

    async def run() -> None:
        openai_client = _RecordingProviderSdkClient()
        openai_adapter = OpenAICompatibleHealthProbe(
            client_factory=lambda **_: openai_client,
        )
        openai_checks = await openai_adapter.probe(
            ModelProviderHealthTarget(
                "openai",
                "test-model",
                "test-key",
                None,
                OPENAI_RESPONSES_API,
            ),
            probes=("models", "inference"),
            timeout_seconds=3,
        )

        chat_client = _RecordingProviderSdkClient()
        chat_adapter = OpenAICompatibleHealthProbe(
            client_factory=lambda **_: chat_client,
        )
        await chat_adapter.probe(
            ModelProviderHealthTarget(
                "openai-compatible",
                "test-model",
                "test-key",
                "https://models.example.test/v1",
                OPENAI_CHAT_COMPLETIONS_API,
            ),
            probes=("inference",),
            timeout_seconds=3,
        )

        anthropic_client = _RecordingProviderSdkClient()
        anthropic_adapter = AnthropicHealthProbe(
            client_factory=lambda **_: anthropic_client,
        )
        anthropic_checks = await anthropic_adapter.probe(
            ModelProviderHealthTarget("anthropic", "test-model", "test-key", None),
            probes=("models", "inference"),
            timeout_seconds=3,
        )

        assert [check.status for check in openai_checks] == ["passed", "passed"]
        assert openai_checks[0].metadata["model_present"] is True
        assert openai_client.operations == ["models", "responses"]
        assert chat_client.operations == ["chat_completions"]
        assert [check.status for check in anthropic_checks] == ["passed", "passed"]
        assert anthropic_client.operations == ["models", "anthropic_messages"]

    asyncio.run(run())


def test_runtime_registry_declares_only_implemented_backends() -> None:
    with Session() as session:
        registry = build_runtime_backend_registry(session, None, None)
        matrix = registry.capabilities()
        assert matrix["docker"].managed_container_lifecycle
        assert not matrix["docker"].asynchronous_jobs
        assert matrix["self_hosted"].asynchronous_jobs
        assert matrix["self_hosted"].mcp_stdio
        assert not matrix["self_hosted"].managed_container_lifecycle
        assert registry.resolve("hosted_sandbox") is None
        assert registry.resolve("unknown") is None


class _RecordingProviderSdkClient:
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.models = _RecordingModels(self.operations)
        self.responses = _RecordingResponses(self.operations)
        self.chat = SimpleNamespace(
            completions=_RecordingChatCompletions(self.operations),
        )
        self.messages = _RecordingAnthropicMessages(self.operations)

    async def __aenter__(self) -> _RecordingProviderSdkClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


class _RecordingModels:
    def __init__(self, operations: list[str]) -> None:
        self._operations = operations

    async def list(self) -> object:
        self._operations.append("models")
        return SimpleNamespace(data=[SimpleNamespace(id="test-model")])


class _RecordingResponses:
    def __init__(self, operations: list[str]) -> None:
        self._operations = operations

    async def create(self, **_: object) -> object:
        self._operations.append("responses")
        return object()


class _RecordingChatCompletions:
    def __init__(self, operations: list[str]) -> None:
        self._operations = operations

    async def create(self, **_: object) -> object:
        self._operations.append("chat_completions")
        return object()


class _RecordingAnthropicMessages:
    def __init__(self, operations: list[str]) -> None:
        self._operations = operations

    async def create(self, **_: object) -> object:
        self._operations.append("anthropic_messages")
        return object()
