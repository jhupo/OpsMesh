"""Native model creation with explicit credentials and a caller-owned lifespan."""

from contextlib import AsyncExitStack

import httpx2
from agents import ModelSettings
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI, DefaultAsyncHttpx2Client
from pydantic import SecretStr

from opsmesh.agents.config.model import ModelRef, model_settings
from opsmesh.shared.network import CredentialResolver, http_routing

from .registry import ProviderRegistry


async def use_server_error_backoff(response: httpx2.Response) -> None:
    """Let the SDK choose its native backoff for explicitly opted-in 5xx responses."""
    if 500 <= response.status_code < 600:
        response.headers.pop("retry-after", None)
        response.headers.pop("retry-after-ms", None)


async def create_model(
    reference: ModelRef,
    *,
    providers: ProviderRegistry,
    credentials: CredentialResolver,
    lifespan: AsyncExitStack,
) -> tuple[Model, ModelSettings]:
    provider, info = providers.select(reference.provider_id, reference.model_id)
    secret = await credentials(provider.credential_ref)
    if not isinstance(secret, SecretStr) or not secret.get_secret_value():
        raise ValueError("Credential resolver must return a nonempty SecretStr")
    trust_env, proxy = await http_routing(provider.network, credentials)
    transport = await lifespan.enter_async_context(
        DefaultAsyncHttpx2Client(
            trust_env=trust_env,
            proxy=proxy,
            event_hooks={"response": [use_server_error_backoff]}
            if provider.server_error_retry_after == "backoff"
            else None,
        )
    )
    client = await lifespan.enter_async_context(
        AsyncOpenAI(
            http_client=transport,
            api_key=secret.get_secret_value(),
            base_url=str(provider.base_url),
            timeout=provider.timeout,
            max_retries=provider.max_retries,
            organization=provider.organization,
            project=provider.project,
        )
    )
    model: Model = (
        OpenAIResponsesModel(model=info.id, openai_client=client)
        if provider.protocol == "openai_responses"
        else OpenAIChatCompletionsModel(model=info.id, openai_client=client)
    )
    return model, model_settings(info.default_settings)
