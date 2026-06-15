from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.db.pagination import page_scalars
from backend.app.model_providers.audit_payloads import (
    base_url_host,
    budget_metadata_with_model_api,
    model_api_audit_payload,
)
from backend.app.model_providers.health import (
    ModelProviderHealthCheckResult,
    ModelProviderHealthTarget,
    ProviderProbeName,
    probe_model_provider,
)
from backend.app.model_providers.health_summary import (
    model_provider_health_check_schedule_summary,
    model_provider_last_health_check_at,
)
from backend.app.model_providers.model_api import (
    model_api_for_provider,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.provider_keys import (
    canonical_model_provider,
)
from backend.app.model_providers.resolver import ModelProviderResolver
from backend.app.model_providers.service_models import (
    ModelProviderUnavailableError,
    ResolvedModelProvider,
)
from backend.app.model_providers.validation import validated_base_url
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import (
    MODEL_PROVIDER_BASE_URL_POLICY,
    EgressUrlPolicy,
)
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text

PROVIDER_UNHEALTHY_FAILURE_THRESHOLD = 3

__all__ = [
    "ModelProviderCredentialService",
    "ModelProviderUnavailableError",
    "ResolvedModelProvider",
    "model_provider_health_check_schedule_summary",
    "model_provider_last_health_check_at",
]


class ModelProviderCredentialService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
        egress_policy: EgressUrlPolicy = MODEL_PROVIDER_BASE_URL_POLICY,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._egress_policy = egress_policy

    def create(
        self,
        *,
        workspace_id: UUID,
        created_by_user_id: UUID,
        name: str,
        provider: str,
        api_key: str,
        default_model: str,
        base_url: str | None,
        is_default: bool,
        model_api: str | None = None,
        budget_metadata: dict[str, object] | None = None,
    ) -> ModelProviderCredential:
        provider = canonical_model_provider(provider)
        base_url = validated_base_url(
            base_url,
            provider=provider,
            egress_policy=self._egress_policy,
        )
        encrypted = self._secret_service.encrypt_payload({"api_key": api_key})
        if is_default:
            self._unset_other_defaults(workspace_id)
        credential = ModelProviderCredential(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            name=name,
            provider=provider,
            base_url=base_url,
            default_model=default_model,
            encrypted_api_key=encrypted.ciphertext,
            api_key_fingerprint=encrypted.fingerprint,
            encryption_key_id=encrypted.key_id,
            is_default=is_default,
            status="active",
            budget_metadata=budget_metadata_with_model_api(
                budget_metadata,
                model_api=model_api,
                model_api_provided=model_api is not None,
                provider=provider,
            ),
        )
        self._session.add(credential)
        self._session.flush()
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=created_by_user_id,
            action="model_provider_credential.created",
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata={
                "name": credential.name,
                "provider": credential.provider,
                "base_url_configured": bool(credential.base_url),
                "base_url_host": base_url_host(credential.base_url),
                "default_model": credential.default_model,
                **model_api_audit_payload(credential),
                "is_default": credential.is_default,
                "budget_configured": bool(credential.budget_metadata),
            },
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

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
    ) -> tuple[list[AuditEvent], int]:
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

    def update(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
        name: str | None = None,
        provider: str | None = None,
        default_model: str | None = None,
        base_url: str | None = None,
        model_api: str | None = None,
        model_api_provided: bool = False,
        is_default: bool | None = None,
        budget_metadata: dict[str, object] | None = None,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        if name is not None:
            credential.name = name
        next_provider = credential.provider
        if provider is not None:
            next_provider = canonical_model_provider(provider)
        if default_model is not None:
            credential.default_model = default_model
        if provider is not None or base_url is not None:
            credential.base_url = validated_base_url(
                base_url if base_url is not None else credential.base_url,
                provider=next_provider,
                egress_policy=self._egress_policy,
            )
            credential.provider = next_provider
        if is_default is not None:
            if is_default:
                self._unset_other_defaults(workspace_id, credential.id)
            credential.is_default = is_default
        if budget_metadata is not None or model_api_provided:
            credential.budget_metadata = budget_metadata_with_model_api(
                budget_metadata if budget_metadata is not None else credential.budget_metadata,
                model_api=model_api,
                model_api_provided=model_api_provided,
                provider=next_provider,
            )
        self._audit(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.updated",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def rotate_key(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
        api_key: str,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        encrypted = self._secret_service.encrypt_payload({"api_key": api_key})
        credential.encrypted_api_key = encrypted.ciphertext
        credential.api_key_fingerprint = encrypted.fingerprint
        credential.encryption_key_id = encrypted.key_id
        self._audit(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.rotated",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def set_default(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        self._unset_other_defaults(workspace_id, credential.id)
        credential.is_default = True
        self._audit(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.default_set",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def disable(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        credential.status = "disabled"
        credential.is_default = False
        self._audit(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.disabled",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def record_success(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
    ) -> None:
        if credential_id is None:
            return
        credential = self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        credential.health_status = "healthy"
        credential.failure_count = 0
        credential.last_success_at = datetime.now(UTC)
        credential.last_failure_code = None
        credential.last_failure_message = None
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
        credential = self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        credential.failure_count += 1
        credential.health_status = (
            "unhealthy"
            if credential.failure_count >= PROVIDER_UNHEALTHY_FAILURE_THRESHOLD
            else "degraded"
        )
        credential.last_failure_at = datetime.now(UTC)
        credential.last_failure_code = error_code[:120]
        credential.last_failure_message = error_message[:1000]
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
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
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
        self._apply_health_check_result(credential, result)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="model_provider_credential.health_checked",
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata={
                "name": credential.name,
                "provider": credential.provider,
                "model": credential.default_model,
                **model_api_audit_payload(credential),
                "status": result.status,
                "checks": [
                    redact_sensitive_payload(check.as_dict())
                    for check in result.checks
                ],
                "base_url_configured": bool(credential.base_url),
                "base_url_host": base_url_host(credential.base_url),
            },
        )
        self._session.commit()
        self._session.refresh(credential)
        return result

    def resolve_for_agent(
        self,
        *,
        workspace_id: UUID,
        agent_credential_id: UUID | None,
        agent_model: str,
    ) -> ResolvedModelProvider:
        return self._resolver().resolve_for_agent(
            workspace_id=workspace_id,
            agent_credential_id=agent_credential_id,
            agent_model=agent_model,
        )

    def resolve_for_review(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        review_model: str,
    ) -> ResolvedModelProvider:
        return self._resolver().resolve_for_review(
            workspace_id=workspace_id,
            credential_id=credential_id,
            review_model=review_model,
        )

    def _resolver(self) -> ModelProviderResolver:
        return ModelProviderResolver(self._session, self._secret_service)

    def _require(self, *, workspace_id: UUID, credential_id: UUID) -> ModelProviderCredential:
        credential = self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            raise ValueError("Model provider credential not found")
        return credential

    def _apply_health_check_result(
        self,
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

    def _audit(
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
            metadata={
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
            },
        )

    def _unset_other_defaults(self, workspace_id: UUID, credential_id: UUID | None = None) -> None:
        statement = update(ModelProviderCredential).where(
            ModelProviderCredential.workspace_id == workspace_id
        )
        if credential_id is not None:
            statement = statement.where(ModelProviderCredential.id != credential_id)
        self._session.execute(
            statement.values(is_default=False)
        )
















