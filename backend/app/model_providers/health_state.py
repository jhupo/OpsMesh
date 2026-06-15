from __future__ import annotations

from datetime import UTC, datetime

from backend.app.model_providers.health import ModelProviderHealthCheckResult
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.security.redaction import redact_sensitive_text

PROVIDER_UNHEALTHY_FAILURE_THRESHOLD = 3


def record_provider_success(credential: ModelProviderCredential) -> None:
    credential.health_status = "healthy"
    credential.failure_count = 0
    credential.last_success_at = datetime.now(UTC)
    credential.last_failure_code = None
    credential.last_failure_message = None


def record_provider_failure(
    credential: ModelProviderCredential,
    *,
    error_code: str,
    error_message: str,
) -> None:
    credential.failure_count += 1
    credential.health_status = (
        "unhealthy"
        if credential.failure_count >= PROVIDER_UNHEALTHY_FAILURE_THRESHOLD
        else "degraded"
    )
    credential.last_failure_at = datetime.now(UTC)
    credential.last_failure_code = error_code[:120]
    credential.last_failure_message = error_message[:1000]


def apply_health_check_result(
    credential: ModelProviderCredential,
    result: ModelProviderHealthCheckResult,
) -> None:
    now = datetime.now(UTC)
    credential.health_status = result.status
    if result.status == "healthy":
        credential.failure_count = 0
        credential.last_success_at = now
        credential.last_failure_code = None
        credential.last_failure_message = None
        return
    credential.failure_count += 1
    credential.last_failure_at = now
    credential.last_failure_code = (result.failure_code or result.status)[:120]
    credential.last_failure_message = (
        redact_sensitive_text(result.failure_message)
        if result.failure_message is not None
        else f"Provider health check is {result.status}."
    )[:1000]


def provider_health_audit_metadata(
    credential: ModelProviderCredential,
    result: ModelProviderHealthCheckResult,
) -> dict[str, object]:
    from backend.app.model_providers.audit_payloads import (
        base_url_host,
        model_api_audit_payload,
    )
    from backend.app.security.redaction import redact_sensitive_payload

    return {
        "name": credential.name,
        "provider": credential.provider,
        "model": credential.default_model,
        **model_api_audit_payload(credential),
        "status": result.status,
        "checks": [redact_sensitive_payload(check.as_dict()) for check in result.checks],
        "base_url_configured": bool(credential.base_url),
        "base_url_host": base_url_host(credential.base_url),
    }


def provider_credential_audit_metadata(
    credential: ModelProviderCredential,
) -> dict[str, object]:
    from backend.app.model_providers.audit_payloads import (
        base_url_host,
        model_api_audit_payload,
    )

    return {
        "name": credential.name,
        "provider": credential.provider,
        "base_url_configured": bool(credential.base_url),
        "base_url_host": base_url_host(credential.base_url),
        "default_model": credential.default_model,
        **model_api_audit_payload(credential),
        "is_default": credential.is_default,
        "status": credential.status,
        "health_status": credential.health_status,
        "failure_count": credential.failure_count,
        "budget_configured": bool(credential.budget_metadata),
    }
