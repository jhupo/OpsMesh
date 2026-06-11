from backend.app.model_providers.provider_keys import is_anthropic_provider

ANTHROPIC_MESSAGES_API = "anthropic_messages"
OPENAI_CHAT_COMPLETIONS_API = "chat_completions"
OPENAI_RESPONSES_API = "responses"
KNOWN_MODEL_APIS = frozenset(
    {
        ANTHROPIC_MESSAGES_API,
        OPENAI_CHAT_COMPLETIONS_API,
        OPENAI_RESPONSES_API,
    }
)

def configured_model_api(metadata: dict[str, object]) -> str | None:
    value = metadata.get("model_api")
    return canonical_model_api(value)


def canonical_model_api(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    return key


def require_known_model_api(value: object) -> str | None:
    canonical = canonical_model_api(value)
    if canonical is None:
        return None
    if canonical not in KNOWN_MODEL_APIS:
        raise ValueError(f"Unsupported model_api: {value}")
    return canonical


def require_provider_model_api(provider: str | None, value: object) -> str | None:
    canonical = require_known_model_api(value)
    if canonical is None:
        return None
    if canonical not in model_api_options_for_provider(provider):
        raise ValueError(f"model_api {canonical} is not supported by provider {provider}")
    return canonical


def default_model_api(provider: str | None) -> str | None:
    if is_anthropic_provider(provider):
        return ANTHROPIC_MESSAGES_API
    return None


def model_api_options_for_provider(provider: str | None) -> tuple[str, ...]:
    if is_anthropic_provider(provider):
        return (ANTHROPIC_MESSAGES_API,)
    return (OPENAI_RESPONSES_API, OPENAI_CHAT_COMPLETIONS_API)


def model_api_for_provider(
    provider: str | None,
    metadata: dict[str, object],
) -> str | None:
    if is_anthropic_provider(provider):
        return ANTHROPIC_MESSAGES_API
    return configured_model_api(metadata) or default_model_api(provider)


def model_api_for_agent_provider(
    provider: str | None,
    agent_metadata: dict[str, object] | None,
    provider_metadata: dict[str, object],
) -> str | None:
    provider_model_api = model_api_for_provider(provider, provider_metadata)
    agent_model_api = configured_model_api(agent_metadata or {})
    if agent_model_api is None:
        return provider_model_api
    if agent_model_api in model_api_options_for_provider(provider):
        return agent_model_api
    raise ValueError(f"model_api {agent_model_api} is not supported by provider {provider}")


def unsupported_agent_model_api(
    provider: str | None,
    agent_metadata: dict[str, object] | None,
) -> str | None:
    agent_model_api = configured_model_api(agent_metadata or {})
    if agent_model_api is None:
        return None
    if agent_model_api in model_api_options_for_provider(provider):
        return None
    return agent_model_api
