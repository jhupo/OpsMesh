"""Native model creation with explicit credentials and a caller-owned lifespan."""

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack

from agents import ModelSettings
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from pydantic import SecretStr

from opsmesh.agents.config.model import ModelRef, model_settings

from .registry import ProviderRegistry

CredentialResolver = Callable[[str], Awaitable[SecretStr]]


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
    client = await lifespan.enter_async_context(
        AsyncOpenAI(
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
