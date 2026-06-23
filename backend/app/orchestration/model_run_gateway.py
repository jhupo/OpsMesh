from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner, AgentRunRequest, AgentRunResult
from backend.app.core.config import Settings
from backend.app.orchestration.model_provider_audit import ModelProviderAuditService
from backend.app.orchestration.model_provider_routing import ModelProviderRoutingService
from backend.app.orchestration.model_request_approval import ModelRequestApprovalService
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_request_builder import RunRequestBuilder
from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload

MarkRunFailed = Callable[[AgentRun, Exception], None]


@dataclass(slots=True)
class ModelRunGateway:
    session: Session
    settings: Settings | None
    agent_runner: AgentRunner
    request_builder: RunRequestBuilder
    events: RunEventRecorder
    mark_run_failed: MarkRunFailed

    async def run_with_provider_fallback(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
    ) -> AgentRunResult | None:
        approval = self.approvals()
        routing = self.routing()
        audit = self.audit()
        if approval.requires_approval(run, request):
            return None
        try:
            return await self.execute_model_request(
                run,
                request,
                job,
                fallback_selected=False,
            )
        except Exception as exc:
            fallback_request = routing.fallback_request(
                run=run,
                job=job,
                failed_request=request,
                exc=exc,
            )
            if fallback_request is None:
                self.mark_run_failed(run, exc)
                self.session.commit()
                raise

        if approval.requires_approval(run, fallback_request):
            return None
        try:
            result = await self.execute_model_request(
                run,
                fallback_request,
                job,
                fallback_selected=True,
            )
        except Exception as fallback_exc:
            self.events.append_model_request_failed_event(run, fallback_request, fallback_exc)
            audit.record_request_failed(run, fallback_request, job, fallback_exc)
            routing.record_failure(
                run,
                fallback_request.model_provider_credential_id,
                fallback_exc,
            )
            self.mark_run_failed(run, fallback_exc)
            self.session.commit()
            raise

        self.events.append_model_provider_fallback_selected_event(
            run,
            failed_request=request,
            selected_request=fallback_request,
        )
        self.events.append_model_provider_used_event(run, fallback_request)
        audit.record_provider_used(run, fallback_request, job, fallback_selected=True)
        return result

    async def execute_model_request(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
        *,
        fallback_selected: bool,
    ) -> AgentRunResult:
        routing = self.routing()
        audit = self.audit()
        try:
            self.events.append_model_request_started_event(
                run,
                request,
                fallback_selected=fallback_selected,
            )
            result = await self.agent_runner.run(request)
            self.events.append_model_response_received_event(run, request, result)
        except Exception as exc:
            self.events.append_model_request_failed_event(run, request, exc)
            audit.record_request_failed(run, request, job, exc)
            routing.record_failure(
                run,
                request.model_provider_credential_id,
                exc,
            )
            raise
        routing.record_success(run, request.model_provider_credential_id)
        if not fallback_selected:
            self.events.append_model_provider_used_event(run, request)
            audit.record_provider_used(run, request, job, fallback_selected=False)
        return result

    def approvals(self) -> ModelRequestApprovalService:
        return ModelRequestApprovalService(
            session=self.session,
            settings=self.settings,
            events=self.events,
        )

    def routing(self) -> ModelProviderRoutingService:
        return ModelProviderRoutingService(
            session=self.session,
            request_builder=self.request_builder,
            events=self.events,
            audit=self.audit(),
        )

    def audit(self) -> ModelProviderAuditService:
        return ModelProviderAuditService(self.session)
