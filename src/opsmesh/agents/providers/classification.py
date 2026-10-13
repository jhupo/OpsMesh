"""Pure provider classification, usable inside Runtime without persistence imports."""

import re

OPENAI_COMPATIBLE_PROVIDERS = frozenset({"openai", "openai-compatible"})


def model_provider_key(provider: str | None) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", (provider or "").strip().lower())
    return key.strip("-")


def canonical_model_provider(provider: str | None) -> str:
    return model_provider_key(provider)


def is_openai_compatible_provider(provider: str | None) -> bool:
    return canonical_model_provider(provider) in OPENAI_COMPATIBLE_PROVIDERS


def require_model_provider(provider: str) -> str:
    canonical = canonical_model_provider(provider)
    if canonical not in OPENAI_COMPATIBLE_PROVIDERS:
        raise ValueError(f"Unsupported model provider: {provider}")
    return canonical
