from __future__ import annotations

from backend.app.model_providers.base_url import normalize_openai_compatible_base_url
from backend.app.model_providers.provider_keys import is_openai_compatible_provider
from backend.app.security.egress import EgressUrlPolicy, validate_egress_url


def validated_base_url(
    base_url: str | None,
    *,
    provider: str | None,
    egress_policy: EgressUrlPolicy,
) -> str | None:
    if base_url is None:
        return None
    validated = validate_egress_url(base_url, policy=egress_policy)
    if is_openai_compatible_provider(provider):
        return normalize_openai_compatible_base_url(validated)
    return validated.rstrip("/")
