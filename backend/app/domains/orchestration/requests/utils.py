from uuid import UUID

from backend.app.core.common.values import uuid_or_none
from backend.app.domains.agents.providers.model_api import (
    canonical_model_api,
    model_api_options_for_provider,
)


def expect_optional_uuid(
    snapshot: dict[str, object],
    key: str,
    expected: UUID | None,
) -> None:
    raw_value = snapshot.get(key)
    if raw_value is None:
        return
    parsed = uuid_or_none(raw_value)
    if parsed != expected:
        raise ValueError(f"Authorization snapshot {key} mismatch")


def model_api_from_settings(settings: dict[str, object] | None) -> str | None:
    if settings is None:
        return None
    value = settings.get("model_api")
    return canonical_model_api(value)


def effective_resolved_model_api(
    *,
    provider: str | None,
    resolved_model_api: str | None,
    requested_model_api: str | None,
    prefer_requested: bool,
) -> str | None:
    requested = canonical_model_api(requested_model_api)
    if (
        prefer_requested
        and requested is not None
        and requested in model_api_options_for_provider(provider)
    ):
        return requested
    return resolved_model_api or requested


