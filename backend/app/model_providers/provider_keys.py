import re

OPENAI_COMPATIBLE_PROVIDERS = {"openai", "openai-compatible"}
ANTHROPIC_PROVIDERS = {"anthropic"}

_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai-compatible": "openai-compatible",
    "anthropic": "anthropic",
}


def model_provider_key(provider: str | None) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", (provider or "").strip().lower())
    return key.strip("-")


def canonical_model_provider(provider: str | None) -> str:
    key = model_provider_key(provider)
    return _PROVIDER_ALIASES.get(key, key)


def is_openai_compatible_provider(provider: str | None) -> bool:
    return canonical_model_provider(provider) in OPENAI_COMPATIBLE_PROVIDERS


def is_anthropic_provider(provider: str | None) -> bool:
    return canonical_model_provider(provider) in ANTHROPIC_PROVIDERS
