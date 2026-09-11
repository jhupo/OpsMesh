from collections.abc import Callable
from dataclasses import dataclass

from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeExecutor,
)
from backend.app.agent_runtime.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimePolicyError,
)
from backend.app.core.config import Settings
from backend.app.core.trace_context import current_trace_context, telemetry_span
from backend.app.observability.cost_service import CostAccountingService, CostBudgetExceededError
from backend.app.orchestration.models_layer.provider_audit import ModelProviderAuditService
from backend.app.orchestration.models_layer.provider_routing import ModelProviderRoutingService
from backend.app.orchestration.models_layer.request_approval import ModelRequestApprovalService
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_request.builder import RunRequestBuilder
from backend.app.runs.models import AgentRun
from backend.app.workers.jobs import JobPayload

MarkRunFailed = Callable[[AgentRun, Exception], None]


@dataclass(slots=True)
class ModelRunGateway:
    session: Session
    settings: Settings | None
    agent_runner: AgentRuntimeExecutor
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
        except CostBudgetExceededError as exc:
            self.mark_run_failed(run, exc)
            self.session.commit()
            return None
        except AgentRuntimeCancelledError:
            raise
        except AgentRuntimePolicyError as exc:
            self.mark_run_failed(run, exc)
            self.session.commit()
            return None
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
        except CostBudgetExceededError as exc:
            self.mark_run_failed(run, exc)
            self.session.commit()
            return None
        except AgentRuntimeCancelledError:
            raise
        except AgentRuntimePolicyError as exc:
            self.mark_run_failed(run, exc)
            self.session.commit()
            return None
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
            CostAccountingService(self.session).assert_budget_available(
                run.workspace_id,
                provider=request.provider or "openai",
                model=request.model or request.agent_profile.model,
            )
        except CostBudgetExceededError as exc:
            self.events.append_event(
                run,
                "cost.budget_blocked",
                "Model request blocked by workspace cost budget",
                {"reason": str(exc)},
            )
            raise
        try:
            self.events.append_model_request_started_event(
                run,
                request,
                fallback_selected=fallback_selected,
            )
            self.session.commit()
            with telemetry_span(
                "opsmesh.model.request",
                parent=current_trace_context(),
                kind=SpanKind.CLIENT,
                attributes={
                    "opsmesh.workspace.id": str(run.workspace_id),
                    "opsmesh.run.id": str(run.id),
                    "gen_ai.provider.name": request.provider or "openai",
                    "gen_ai.request.model": request.model or request.agent_profile.model,
                    "opsmesh.model.fallback": fallback_selected,
                },
            ):
                result = await self.agent_runner.run(request)
            usage_record = CostAccountingService(self.session).record_usage(
                run=run,
                request=request,
                result=result,
                job_attempt=job.attempt,
            )
            self.events.append_event(
                run,
                "cost.usage_recorded",
                "Model usage and cost recorded",
                {
                    "model_usage_record_id": str(usage_record.id),
                    "metering_status": usage_record.metering_status,
                    "currency": usage_record.currency,
                    "total_cost": str(usage_record.total_cost)
                    if usage_record.total_cost is not None
                    else None,
                    "total_tokens": usage_record.total_tokens,
                    "job_attempt": usage_record.job_attempt,
                },
            )
            self.events.append_model_response_received_event(run, request, result)
        except AgentRuntimeCancelledError:
            self.events.append_event(
                run,
                "model.request_cancelled",
                "Cancellation reached the active agent SDK run",
                {
                    "model": request.model,
                    "provider": request.provider,
                    "propagated": True,
                },
            )
            raise
        except Exception as exc:
            self.events.append_model_request_failed_event(run, request, exc)
            audit.record_request_failed(run, request, job, exc)
            if not isinstance(exc, AgentRuntimePolicyError):
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
