from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_text
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.agents.providers.audit import ModelProviderAuditWriter
from backend.app.domains.agents.providers.contracts import ProviderProbeName
from backend.app.domains.agents.providers.model_api import model_api_for_provider
from backend.app.domains.agents.providers.models import ModelProviderCredential
from backend.app.domains.agents.providers.probes import (
    ModelProviderHealthCheckResult,
    ModelProviderHealthTarget,
    probe_model_provider,
)


class ModelProviderHealthService:
    def __init__(self, session: Session, secret_service: SecretEncryptionService) -> None:
        self._session = session
        self._secret_service = secret_service

    def record_success(self, *, workspace_id: UUID, credential_id: UUID | None) -> None:
        if credential_id is None:
            return
        credential = self._queries().get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        record_provider_success(credential)
        self._session.flush([credential])

    def record_failure(
        self, *, workspace_id: UUID, credential_id: UUID | None, error_code: str, error_message: str
    ) -> None:
        if credential_id is None:
            return
        credential = self._queries().get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        record_provider_failure(credential, error_code=error_code, error_message=error_message)
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
        credential = self._queries().require(workspace_id=workspace_id, credential_id=credential_id)
        payload = self._secret_service.decrypt_payload(credential.encrypted_api_key)
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Model provider credential is missing api_key")
        model_api = model_api_for_provider(credential.provider, credential.budget_metadata)
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
            workspace_id=workspace_id, user_id=actor_user_id, credential=credential, result=result
        )
        self._session.commit()
        self._session.refresh(credential)
        return result

    def _queries(self) -> ModelProviderCredentialQueryService:
        from backend.app.domains.agents.providers.queries import ModelProviderCredentialQueryService

        return ModelProviderCredentialQueryService(self._session, self._secret_service)


PROVIDER_UNHEALTHY_FAILURE_THRESHOLD = 3


def record_provider_success(credential: ModelProviderCredential) -> None:
    credential.health_status = "healthy"
    credential.failure_count = 0
    credential.last_success_at = datetime.now(UTC)
    credential.last_failure_code = None
    credential.last_failure_message = None


def record_provider_failure(
    credential: ModelProviderCredential, *, error_code: str, error_message: str
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
    credential: ModelProviderCredential, result: ModelProviderHealthCheckResult
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
    credential: ModelProviderCredential, result: ModelProviderHealthCheckResult
) -> dict[str, object]:
    from backend.app.core.security.redaction import redact_sensitive_payload
    from backend.app.domains.agents.providers.audit import model_api_audit_payload
    from backend.app.domains.agents.providers.policy import model_provider_base_url_host

    return {
        "name": credential.name,
        "provider": credential.provider,
        "model": credential.default_model,
        **model_api_audit_payload(credential),
        "status": result.status,
        "checks": [redact_sensitive_payload(check.as_dict()) for check in result.checks],
        "base_url_configured": bool(credential.base_url),
        "base_url_host": model_provider_base_url_host(credential.base_url),
    }


def provider_credential_audit_metadata(credential: ModelProviderCredential) -> dict[str, object]:
    from backend.app.domains.agents.providers.audit import model_api_audit_payload
    from backend.app.domains.agents.providers.policy import model_provider_base_url_host

    return {
        "name": credential.name,
        "provider": credential.provider,
        "base_url_configured": bool(credential.base_url),
        "base_url_host": model_provider_base_url_host(credential.base_url),
        "default_model": credential.default_model,
        **model_api_audit_payload(credential),
        "is_default": credential.is_default,
        "status": credential.status,
        "health_status": credential.health_status,
        "failure_count": credential.failure_count,
        "budget_configured": bool(credential.budget_metadata),
    }


_HEALTH_CHECK_SCHEDULE_JOB_LIMIT = 10
if TYPE_CHECKING:
    from backend.app.domains.agents.providers.queries import ModelProviderCredentialQueryService
    from backend.app.runtime.workers.scheduling.models import WorkspaceScheduledJob


def model_provider_last_health_check_at(credential: ModelProviderCredential) -> datetime | None:
    timestamps = [
        value
        for value in (credential.last_success_at, credential.last_failure_at)
        if value is not None
    ]
    return max(timestamps) if timestamps else None


def model_provider_health_check_schedule_summary(
    session: Session, *, workspace_id: UUID, credential_id: UUID
) -> dict[str, object]:
    from backend.app.runtime.workers.contracts import JobType
    from backend.app.runtime.workers.scheduling.models import WorkspaceScheduledJob

    jobs = list(
        session.scalars(
            select(WorkspaceScheduledJob)
            .where(
                WorkspaceScheduledJob.workspace_id == workspace_id,
                WorkspaceScheduledJob.resource_id == credential_id,
                WorkspaceScheduledJob.job_type == JobType.MODEL_PROVIDER_HEALTH_CHECK.value,
            )
            .order_by(
                WorkspaceScheduledJob.status.asc(),
                WorkspaceScheduledJob.next_run_at.asc(),
                WorkspaceScheduledJob.created_at.desc(),
            )
            .limit(_HEALTH_CHECK_SCHEDULE_JOB_LIMIT)
        )
    )
    active_jobs = [job for job in jobs if job.status == "active"]
    active_next_runs = [job.next_run_at for job in active_jobs if job.next_run_at is not None]
    return {
        "configured": bool(jobs),
        "active_count": len(active_jobs),
        "paused_count": sum(1 for job in jobs if job.status == "paused"),
        "next_run_at": min(active_next_runs) if active_next_runs else None,
        "jobs": [health_check_schedule_job_summary(job) for job in jobs],
    }


def health_check_schedule_job_summary(job: WorkspaceScheduledJob) -> dict[str, object]:
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status,
        "schedule_type": job.schedule_type,
        "next_run_at": job.next_run_at,
        "last_run_at": job.last_run_at,
        "routing": health_check_schedule_routing_summary(job.routing),
    }


def health_check_schedule_routing_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    summary: dict[str, object] = {}
    probes = value.get("probes")
    if isinstance(probes, list) and all(isinstance(item, str) for item in probes):
        summary["probes"] = probes
    timeout_seconds = value.get("timeout_seconds")
    if isinstance(timeout_seconds, int | float) and (not isinstance(timeout_seconds, bool)):
        summary["timeout_seconds"] = timeout_seconds
    return summary
