from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import AgentRunRequest
from backend.app.agent_runtime.core.errors import normalize_agent_error
from backend.app.observability.audit_service import AuditService
from backend.app.orchestration.models_layer.request_reviewing import model_provider_request_snapshot
from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload


@dataclass(slots=True)
class ModelProviderAuditService:
    session: Session

    def record_provider_used(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
        *,
        fallback_selected: bool,
    ) -> None:
        if job.requested_by_user_id is None:
            return
        AuditService(self.session).record_user_action(
            workspace_id=run.workspace_id,
            user_id=job.requested_by_user_id,
            action="model_provider.used",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                **run_scope_metadata(run),
                "model": request.model,
                "model_api": request.model_api,
                "provider": request.provider,
                "credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "fallback_selected": fallback_selected,
            },
        )

    def record_request_failed(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
        exc: Exception,
    ) -> None:
        if job.requested_by_user_id is None:
            return
        error = normalize_agent_error(exc)
        AuditService(self.session).record_user_action(
            workspace_id=run.workspace_id,
            user_id=job.requested_by_user_id,
            action="model_provider.request_failed",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                **run_scope_metadata(run),
                "model": request.model,
                "model_api": request.model_api,
                "provider": request.provider,
                "credential_id": str(request.model_provider_credential_id)
                if request.model_provider_credential_id is not None
                else None,
                "reason": error.as_dict(),
            },
        )

    def record_fallback_unavailable(
        self,
        run: AgentRun,
        *,
        failed_request: AgentRunRequest,
        exc: Exception,
    ) -> None:
        if failed_request.context.user_id is None:
            return
        error = normalize_agent_error(exc)
        AuditService(self.session).record_user_action(
            workspace_id=run.workspace_id,
            user_id=failed_request.context.user_id,
            action="model_provider.fallback_unavailable",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                **run_scope_metadata(run),
                "reason": error.as_dict(),
                "failed_provider": model_provider_request_snapshot(failed_request),
            },
        )


def run_scope_metadata(run: AgentRun) -> dict[str, object]:
    return {
        "task_id": str(run.task_id) if run.task_id is not None else None,
        "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
        "agent_profile_id": str(run.agent_profile_id)
        if run.agent_profile_id is not None
        else None,
    }
