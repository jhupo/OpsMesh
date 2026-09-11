from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import AgentRunRequest
from backend.app.agent_runtime.core.errors import normalize_agent_error
from backend.app.core.typing import optional_string, uuid_or_none
from backend.app.model_providers.health_service import ModelProviderHealthService
from backend.app.model_providers.model_api import canonical_model_api
from backend.app.model_providers.resolution_service import ModelProviderResolutionService
from backend.app.model_providers.service_models import ModelProviderUnavailableError
from backend.app.orchestration.model_provider_audit import ModelProviderAuditService
from backend.app.orchestration.model_request_reviewing import model_provider_fallback_policy
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_request_builder import RunRequestBuilder
from backend.app.orchestration.run_request_utils import effective_resolved_model_api
from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload
from backend.app.workspaces.models import Workspace


@dataclass(slots=True)
class ModelProviderRoutingService:
    session: Session
    request_builder: RunRequestBuilder
    events: RunEventRecorder
    audit: ModelProviderAuditService

    def fallback_request(
        self,
        *,
        run: AgentRun,
        job: JobPayload,
        failed_request: AgentRunRequest,
        exc: Exception,
    ) -> AgentRunRequest | None:
        override = self.fallback_override(
            run=run,
            failed_request=failed_request,
            exc=exc,
        )
        if override is None:
            return None
        request = self.request_builder.build_agent_request(
            run,
            job,
            model_provider_override=override,
        )
        self.events.append_context_built_event(run, request)
        return request

    def fallback_override(
        self,
        *,
        run: AgentRun,
        failed_request: AgentRunRequest,
        exc: Exception,
    ) -> dict[str, Any] | None:
        policy = model_provider_fallback_policy(self.workspace_settings(run.workspace_id))
        if policy is None:
            return None
        normalized_error = normalize_agent_error(exc)
        if not normalized_error.retryable:
            return None
        error_code = normalized_error.code
        retry_error_codes = policy.get("retry_error_codes")
        if isinstance(retry_error_codes, list) and retry_error_codes:
            allowed_codes = {item for item in retry_error_codes if isinstance(item, str)}
            if error_code not in allowed_codes:
                return None
        candidates = policy.get("candidates")
        if not isinstance(candidates, list):
            return None
        for candidate in candidates:
            override = self.fallback_candidate_override(
                workspace_id=run.workspace_id,
                failed_request=failed_request,
                candidate=candidate,
            )
            if override is not None:
                return override
        self.events.append_model_provider_fallback_unavailable_event(
            run,
            failed_request=failed_request,
            exc=exc,
        )
        self.audit.record_fallback_unavailable(
            run,
            failed_request=failed_request,
            exc=exc,
        )
        return None

    def fallback_candidate_override(
        self,
        *,
        workspace_id: UUID,
        failed_request: AgentRunRequest,
        candidate: object,
    ) -> dict[str, Any] | None:
        if not isinstance(candidate, dict):
            return None
        credential_id = uuid_or_none(candidate.get("credential_id"))
        if credential_id is None or credential_id == failed_request.model_provider_credential_id:
            return None
        model = optional_string(candidate.get("model"))
        if model is None:
            return None
        try:
            override = self.resolve_model_provider(
                workspace_id=workspace_id,
                credential_id=credential_id,
                agent_model=model,
                model_api=canonical_model_api(candidate.get("model_api")),
                prefer_model_api="model_api" in candidate,
            )
        except (ModelProviderUnavailableError, ValueError):
            return None
        if override["provider"] == failed_request.provider:
            return None
        return override

    def workspace_settings(self, workspace_id: UUID) -> dict[str, object]:
        workspace = self.session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        return settings if isinstance(settings, dict) else {}

    def resolve_model_provider(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        agent_model: str,
        model_api: str | None = None,
        prefer_model_api: bool = False,
    ) -> dict[str, Any]:
        model_api = canonical_model_api(model_api)
        resolved = ModelProviderResolutionService(
            self.session,
            self.request_builder.secret_service(),
        ).resolve_for_agent(
            workspace_id=workspace_id,
            agent_credential_id=credential_id,
            agent_model=agent_model,
        )
        return {
            "model": resolved.model,
            "provider": resolved.provider,
            "base_url": resolved.base_url,
            "api_key": resolved.api_key,
            "model_api": effective_resolved_model_api(
                provider=resolved.provider,
                resolved_model_api=resolved.model_api,
                requested_model_api=model_api,
                prefer_requested=prefer_model_api,
            ),
            "model_provider_credential_id": resolved.credential_id,
        }

    def record_success(self, run: AgentRun, credential_id: UUID | None) -> None:
        ModelProviderHealthService(
            self.session,
            self.request_builder.secret_service(),
        ).record_success(workspace_id=run.workspace_id, credential_id=credential_id)

    def record_failure(
        self,
        run: AgentRun,
        credential_id: UUID | None,
        exc: Exception,
    ) -> None:
        error = normalize_agent_error(exc)
        ModelProviderHealthService(
            self.session,
            self.request_builder.secret_service(),
        ).record_failure(
            workspace_id=run.workspace_id,
            credential_id=credential_id,
            error_code=error.code,
            error_message=error.message,
        )
