from backend.app.api.schemas.model_providers import ModelProviderUsageAuditResponse
from backend.app.observability.audit_models import AuditEvent
from backend.app.model_providers.model_api import canonical_model_api
from backend.app.security.redaction import redact_sensitive_payload_item

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
