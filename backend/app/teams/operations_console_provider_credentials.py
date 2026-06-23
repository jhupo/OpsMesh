from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.model_providers.availability import (
    credential_is_selectable,
    credential_not_selectable_reasons,
)
from backend.app.model_providers.capabilities import (
    list_model_capabilities,
    resolve_model_capability,
)
from backend.app.model_providers.health_summary import (
    model_provider_health_check_schedule_summary,
    model_provider_last_health_check_at,
)
from backend.app.model_providers.metadata import budget_is_exhausted, sanitize_budget_metadata
from backend.app.model_providers.model_api import (
    default_model_api,
    model_api_for_provider,
    model_api_options_for_provider,
)
from backend.app.model_providers.models import ModelProviderCredential


def _model_provider_credential_option_payload(
    credential: ModelProviderCredential,
) -> dict[str, object]:
    session = Session.object_session(credential)
    schedule = (
        model_provider_health_check_schedule_summary(
            session,
            workspace_id=credential.workspace_id,
            credential_id=credential.id,
        )
        if session is not None
        else _empty_health_check_schedule_payload()
    )
    capability = resolve_model_capability(credential.provider, credential.default_model)
    return {
        "id": credential.id,
        "name": credential.name,
        "provider": credential.provider,
        "default_model": credential.default_model,
        "model_options": [
            capability.as_dict()
            for capability in list_model_capabilities(provider=credential.provider)
        ],
        "model_api": model_api_for_provider(
            credential.provider,
            credential.budget_metadata,
        ),
        "model_apis": list(model_api_options_for_provider(credential.provider)),
        "default_model_api": default_model_api(credential.provider),
        "model_capability": capability.as_dict() if capability is not None else None,
        "status": credential.status,
        "health_status": credential.health_status,
        "failure_count": credential.failure_count,
        "budget_exhausted": budget_is_exhausted(credential.budget_metadata),
        "is_default": credential.is_default,
        "base_url_configured": bool(credential.base_url),
        "base_url_host": _base_url_host(credential.base_url),
        "api_key_fingerprint": credential.api_key_fingerprint,
        "last_health_check_at": model_provider_last_health_check_at(credential),
        "scheduled_health_check": schedule,
        "selectable": _credential_selectable(credential),
        "not_selectable_reasons": _credential_not_selectable_reasons(credential),
        "budget_metadata": sanitize_budget_metadata(credential.budget_metadata),
    }


def _model_api_options_payload(provider: object) -> list[str]:
    if not isinstance(provider, str) or not provider.strip():
        return []
    return list(model_api_options_for_provider(provider))


def _default_model_api_payload(provider: object) -> str | None:
    if not isinstance(provider, str) or not provider.strip():
        return None
    return default_model_api(provider)


def _active_credential_ids(credentials: object) -> list[UUID]:
    return [
        credential.id
        for credential in credentials
        if isinstance(credential, ModelProviderCredential)
        and _credential_selectable(credential)
    ]


def _credential_selectable(credential: ModelProviderCredential) -> bool:
    return credential_is_selectable(credential)


def _credential_not_selectable_reasons(
    credential: ModelProviderCredential,
) -> list[str]:
    return credential_not_selectable_reasons(credential)


def _base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    return parsed.netloc or None


def _empty_health_check_schedule_payload() -> dict[str, object]:
    return {
        "configured": False,
        "active_count": 0,
        "paused_count": 0,
        "next_run_at": None,
        "jobs": [],
    }
