import asyncio

import httpx
from sqlalchemy.orm import Session

from backend.app.model_providers.health import (
    AnthropicHealthProbe,
    ModelProviderHealthTarget,
    OpenAICompatibleHealthProbe,
    ProviderHealthRegistry,
)
from backend.app.runtime_manager.backends import build_runtime_backend_registry


def test_health_registry_selects_provider_and_calls_models_endpoint() -> None:
    registry = ProviderHealthRegistry()
    assert isinstance(registry.resolve("OpenAI"), OpenAICompatibleHealthProbe)
    assert isinstance(registry.resolve("anthropic"), AnthropicHealthProbe)
    assert registry.resolve("unknown") is None

    async def run() -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for provider in ("openai", "anthropic"):
                adapter = registry.resolve(provider)
                assert adapter is not None
                checks = await adapter.probe(
                    client,
                    ModelProviderHealthTarget(provider, "test-model", "test-key", None),
                    probes=("models",),
                )
                assert checks[0].status == "passed"
                assert checks[0].metadata["model_present"] is True

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
