from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.model_providers.audit_writer import ModelProviderAuditWriter
from backend.app.model_providers.credential_queries import ModelProviderCredentialQueryService
from backend.app.model_providers.health import (
    ModelProviderHealthCheckResult,
    ModelProviderHealthTarget,
    ProviderProbeName,
    probe_model_provider,
)
from backend.app.model_providers.health_state import (
    apply_health_check_result,
    record_provider_failure,
    record_provider_success,
)
from backend.app.model_providers.model_api import model_api_for_provider
from backend.app.secrets.service import SecretEncryptionService


class ModelProviderHealthService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
    ) -> None:
        self._session = session
        self._secret_service = secret_service

    def record_success(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
    ) -> None:
        if credential_id is None:
            return
        credential = self._queries().get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        record_provider_success(credential)
        self._session.flush([credential])

    def record_failure(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        error_code: str,
        error_message: str,
    ) -> None:
        if credential_id is None:
            return
        credential = self._queries().get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        record_provider_failure(
            credential,
            error_code=error_code,
            error_message=error_message,
        )
        self._session.flush([credential])

    async def run_health_check(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
        probes: tuple[ProviderProbeName, ...] = ("models", "inference"),
        timeout_seconds: float = 15,
    ) -> ModelProviderHealthCheckResult:
        credential = self._queries().require(
            workspace_id=workspace_id,
            credential_id=credential_id,
        )
        payload = self._secret_service.decrypt_payload(credential.encrypted_api_key)
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Model provider credential is missing api_key")
        model_api = model_api_for_provider(
            credential.provider,
            credential.budget_metadata,
        )
        result = await probe_model_provider(
            ModelProviderHealthTarget(
                provider=credential.provider,
                model=credential.default_model,
                api_key=api_key,
                base_url=credential.base_url,
                model_api=model_api,
            ),
            probes=probes,
            timeout_seconds=timeout_seconds,
        )
        apply_health_check_result(credential, result)
        ModelProviderAuditWriter(self._session).health_checked(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            result=result,
        )
        self._session.commit()
        self._session.refresh(credential)
        return result

    def _queries(self) -> ModelProviderCredentialQueryService:
        return ModelProviderCredentialQueryService(self._session, self._secret_service)
