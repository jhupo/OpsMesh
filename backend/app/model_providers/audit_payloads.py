from __future__ import annotations

from backend.app.model_providers.metadata import sanitize_budget_metadata
from backend.app.model_providers.model_api import (
    default_model_api,
    model_api_for_provider,
    model_api_options_for_provider,
    require_provider_model_api,
)
from backend.app.model_providers.models import ModelProviderCredential


def model_api_audit_payload(credential: ModelProviderCredential) -> dict[str, object]:
    return {
        "model_api": model_api_for_provider(
            credential.provider,
            credential.budget_metadata,
        ),
        "model_apis": list(model_api_options_for_provider(credential.provider)),
        "default_model_api": default_model_api(credential.provider),
    }


def budget_metadata_with_model_api(
    metadata: dict[str, object] | None,
    *,
    model_api: object,
    model_api_provided: bool,
    provider: str,
) -> dict[str, object]:
    sanitized = sanitize_budget_metadata(metadata)
    if not model_api_provided:
        configured = sanitized.get("model_api")
        if configured is not None:
            sanitized["model_api"] = require_provider_model_api(provider, configured)
        return sanitized
    canonical = require_provider_model_api(provider, model_api)
    if canonical is None:
        sanitized.pop("model_api", None)
    else:
        sanitized["model_api"] = canonical
    return sanitized
