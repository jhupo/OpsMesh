"""Provider settings reach the native SDK without a second retry loop."""

from contextlib import AsyncExitStack
from unittest.mock import patch

import httpx2
import pytest
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from opsmesh.agents.config.model import ModelRef
from opsmesh.agents.providers.config import ProviderConfig
from opsmesh.agents.providers.factory import create_model, use_server_error_backoff
from opsmesh.agents.providers.models import ModelInfo
from opsmesh.agents.providers.registry import ProviderRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 2, 5])
async def test_configured_retry_limit_reaches_the_native_client(retries):
    providers = ProviderRegistry()
    providers.register(
        ProviderConfig(
            id="p",
            name="Provider",
            credential_ref="env:MODEL_KEY",
            models=[ModelInfo(id="m")],
            timeout=15,
            max_retries=retries,
        )
    )

    async def credential(reference):
        assert reference == "env:MODEL_KEY"
        return SecretStr("test-only-value")

    with patch("opsmesh.agents.providers.factory.AsyncOpenAI", wraps=AsyncOpenAI) as client:
        async with AsyncExitStack() as lifespan:
            model, _ = await create_model(
                ModelRef(provider_id="p", model_id="m"),
                providers=providers,
                credentials=credential,
                lifespan=lifespan,
            )
            assert isinstance(model, OpenAIResponsesModel)
        assert client.call_args.kwargs["max_retries"] == retries
        assert client.call_args.kwargs["timeout"] == 15


@pytest.mark.parametrize(
    "invalid",
    [
        {"max_retries": -1},
        {"max_retries": 11},
        {"request_timeout": 331},
        {"request_timeout": float("inf")},
    ],
)
def test_retry_and_deadline_limits_are_finite_and_bounded(invalid):
    with pytest.raises(ValidationError):
        ProviderConfig(
            id="p",
            name="Provider",
            credential_ref="env:MODEL_KEY",
            models=[ModelInfo(id="m")],
            **invalid,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_backoff_override_preserves_rate_limit_waits(status):
    response = httpx2.Response(status, headers={"Retry-After": "60", "retry-after-ms": "60000"})
    await use_server_error_backoff(response)
    if status == 429:
        assert response.headers["retry-after"] == "60"
        assert response.headers["retry-after-ms"] == "60000"
    else:
        assert "retry-after" not in response.headers
        assert "retry-after-ms" not in response.headers
