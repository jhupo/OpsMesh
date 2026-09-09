from __future__ import annotations

import builtins
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.audit.models import AuditEvent
from backend.app.db.pagination import page_scalars
from backend.app.model_providers.health_summary import (
    model_provider_health_check_schedule_summary,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.resolver import ModelProviderResolver
from backend.app.secrets.service import SecretEncryptionService


class ModelProviderCredentialQueryService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
    ) -> None:
        self._session = session
        self._secret_service = secret_service

    def list(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[ModelProviderCredential], int]:
        statement = (
            select(ModelProviderCredential)
            .where(ModelProviderCredential.workspace_id == workspace_id)
            .order_by(ModelProviderCredential.created_at.desc())
        )
        return page_scalars(self._session, statement, page)

    def list_usage_audit(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        action: str | None = None,
    ) -> tuple[builtins.list[AuditEvent], int]:
        allowed_actions = {
            "model_provider.used",
            "model_provider.request_failed",
            "model_provider.fallback_unavailable",
        }
        statement = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action.in_(allowed_actions),
        )
        if action is not None:
            if action not in allowed_actions:
                return [], 0
            statement = statement.where(AuditEvent.action == action)
        statement = statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        return page_scalars(self._session, statement, page)

    def health_check_schedule_summary(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> dict[str, object]:
        return model_provider_health_check_schedule_summary(
            self._session,
            workspace_id=workspace_id,
            credential_id=credential_id,
        )

    def get(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> ModelProviderCredential | None:
        return self._resolver().get_active(
            workspace_id=workspace_id,
            credential_id=credential_id,
        )

    def require(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> ModelProviderCredential:
        credential = self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            raise ValueError("Model provider credential not found")
        return credential

    def _resolver(self) -> ModelProviderResolver:
        return ModelProviderResolver(self._session, self._secret_service)
