from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload_item
from backend.app.domains.agents.providers.metadata import sanitize_budget_metadata
from backend.app.domains.agents.providers.model_api import (
    canonical_model_api,
    default_model_api,
    model_api_for_provider,
    model_api_options_for_provider,
    require_provider_model_api,
)
from backend.app.domains.agents.providers.models import ModelProviderCredential
from backend.app.domains.agents.providers.policy import model_provider_base_url_host
from backend.app.domains.agents.providers.probes import ModelProviderHealthCheckResult
from backend.app.observability.audit.models import AuditEvent
from backend.app.observability.audit.service import AuditService

"Model provider feature package."


class ModelProviderUsageAuditResponse(BaseModel):
    id: UUID
    action: str
    run_id: str | None
    task_id: str | None
    task_step_id: str | None
    agent_profile_id: str | None
    provider: str | None
    model: str | None
    model_api: str | None
    credential_id: str | None
    fallback_selected: bool | None
    reason: dict[str, object] | None = None
    failed_provider: dict[str, object] | None = None
    created_at: datetime


def model_api_audit_payload(credential: ModelProviderCredential) -> dict[str, object]:
    return {
        "model_api": model_api_for_provider(credential.provider, credential.budget_metadata),
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


_SENSITIVE_METADATA_KEYS = {
    "api_key",
    "authorization",
    "base_url",
    "encrypted_api_key",
    "external_ref",
    "secret",
    "token",
}


def usage_audit_response(event: AuditEvent) -> ModelProviderUsageAuditResponse:
    metadata = event.audit_metadata
    metadata = metadata if isinstance(metadata, dict) else {}
    failed_provider = metadata.get("failed_provider")
    reason = metadata.get("reason")
    return ModelProviderUsageAuditResponse(
        id=event.id,
        action=event.action,
        run_id=str(event.target_id) or None,
        task_id=_string_or_none(metadata.get("task_id")),
        task_step_id=_string_or_none(metadata.get("task_step_id")),
        agent_profile_id=_string_or_none(metadata.get("agent_profile_id")),
        provider=_string_or_none(metadata.get("provider")),
        model=_string_or_none(metadata.get("model")),
        model_api=_model_api_or_none(metadata.get("model_api")),
        credential_id=_string_or_none(metadata.get("credential_id")),
        fallback_selected=metadata.get("fallback_selected")
        if isinstance(metadata.get("fallback_selected"), bool)
        else None,
        reason=sanitized_metadata(reason),
        failed_provider=sanitized_provider_ref(failed_provider),
        created_at=event.created_at,
    )


def sanitized_metadata(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    sanitized: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text.lower() in _SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(item, dict):
            nested = sanitized_metadata(item)
            sanitized[key_text] = nested if nested is not None else {}
        elif isinstance(item, list):
            sanitized[key_text] = [
                sanitized_metadata(entry)
                if isinstance(entry, dict)
                else redact_sensitive_payload_item(entry)
                for entry in item
            ]
        else:
            sanitized[key_text] = redact_sensitive_payload_item(item)
    return sanitized


def sanitized_provider_ref(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    sanitized: dict[str, object] = {}
    for key in ("provider", "model", "model_api", "credential_id"):
        item = value.get(key)
        if key == "model_api":
            model_api = _model_api_or_none(item)
            if model_api is not None:
                sanitized[key] = model_api
        elif isinstance(item, str):
            sanitized[key] = item
    return sanitized


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _model_api_or_none(value: object) -> str | None:
    return canonical_model_api(value)


class ModelProviderAuditWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def credential_created(
        self, *, workspace_id: UUID, user_id: UUID, credential: ModelProviderCredential
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="model_provider_credential.created",
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata={
                "name": credential.name,
                "provider": credential.provider,
                "base_url_configured": bool(credential.base_url),
                "base_url_host": model_provider_base_url_host(credential.base_url),
                "default_model": credential.default_model,
                **model_api_audit_payload(credential),
                "is_default": credential.is_default,
                "budget_configured": bool(credential.budget_metadata),
            },
        )

    def credential_changed(
        self, *, workspace_id: UUID, user_id: UUID, credential: ModelProviderCredential, action: str
    ) -> None:
        from backend.app.domains.agents.providers.health import provider_credential_audit_metadata

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata=provider_credential_audit_metadata(credential),
        )

    def health_checked(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        credential: ModelProviderCredential,
        result: ModelProviderHealthCheckResult,
    ) -> None:
        from backend.app.domains.agents.providers.health import provider_health_audit_metadata

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="model_provider_credential.health_checked",
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata=provider_health_audit_metadata(credential, result),
        )
