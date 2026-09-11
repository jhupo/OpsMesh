from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.model_providers.audit_payloads import model_api_audit_payload
from backend.app.model_providers.base_url import model_provider_base_url_host
from backend.app.model_providers.health import ModelProviderHealthCheckResult
from backend.app.model_providers.health_state import (
    provider_credential_audit_metadata,
    provider_health_audit_metadata,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.observability.audit_service import AuditService


class ModelProviderAuditWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def credential_created(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        credential: ModelProviderCredential,
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
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        credential: ModelProviderCredential,
        action: str,
    ) -> None:
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
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="model_provider_credential.health_checked",
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata=provider_health_audit_metadata(credential, result),
        )
