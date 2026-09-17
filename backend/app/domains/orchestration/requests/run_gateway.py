from collections.abc import Callable
from dataclasses import dataclass

from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.domains.agents.runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeExecutor,
)
from backend.app.domains.agents.runtime.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimePolicyError,
    AgentRuntimeProviderError,
)
from backend.app.domains.orchestration.requests.builder import RunRequestBuilder
from backend.app.domains.orchestration.requests.provider_audit import ModelProviderAuditService
from backend.app.domains.orchestration.requests.provider_routing import ModelProviderRoutingService
from backend.app.domains.orchestration.requests.request_approval import ModelRequestApprovalService
from backend.app.domains.orchestration.runs.events import RunEventRecorder
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.observability.costs.service import CostAccountingService, CostBudgetExceededError
from backend.app.observability.telemetry.trace_context import current_trace_context, telemetry_span
from backend.app.runtime.workers.contracts import JobPayload

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
            if not isinstance(exc, AgentRuntimeProviderError):
                self.mark_run_failed(run, exc)
                self.session.commit()
                raise
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
            self.mark_run_failed(run, fallback_exc)
            self.session.commit()
            raise

        self.events.append_model_provider_fallback_selected_event(
            run,
            failed_request=request,
            selected_request=fallback_request,
        )
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
        costs = CostAccountingService(self.session)
        try:
            budget_decision = costs.assert_budget_available(
                run.workspace_id,
                provider=request.provider or "openai",
                model=request.model or request.agent_profile.model,
            )
        except CostBudgetExceededError as exc:
            self.events.append_event(
                run,
                "cost.budget_blocked",
                "Model request blocked by workspace cost budget",
                {
                    "reason": str(exc),
                    "budget_decision": exc.decision.snapshot()
                    if exc.decision is not None
                    else None,
                },
            )
            if exc.decision is not None:
                costs.record_budget_blocked(
                    run=run,
                    request=request,
                    decision=exc.decision,
                )
            raise
        self.events.append_model_request_started_event(
            run,
            request,
            fallback_selected=fallback_selected,
        )
        self.session.commit()
        request_sequence = 1 if fallback_selected else 0
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
            try:
                result = await self.agent_runner.run(request)
            except AgentRuntimeCancelledError as exc:
                costs.record_attempt(
                    run=run,
                    request=request,
                    result=None,
                    job_attempt=job.attempt,
                    request_sequence=request_sequence,
                    attempt_outcome="cancelled",
                    budget_decision=budget_decision,
                    error=exc,
                )
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
                self.session.commit()
                raise
            except Exception as exc:
                costs.record_attempt(
                    run=run,
                    request=request,
                    result=None,
                    job_attempt=job.attempt,
                    request_sequence=request_sequence,
                    attempt_outcome="failed",
                    budget_decision=budget_decision,
                    error=exc,
                )
                self.events.append_model_request_failed_event(run, request, exc)
                audit.record_request_failed(run, request, job, exc)
                if isinstance(exc, AgentRuntimeProviderError):
                    routing.record_failure(
                        run,
                        request.model_provider_credential_id,
                        exc,
                    )
                self.session.commit()
                raise
            usage_record = costs.record_attempt(
                run=run,
                request=request,
                result=result,
                job_attempt=job.attempt,
                request_sequence=request_sequence,
                attempt_outcome="succeeded",
                budget_decision=budget_decision,
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
                    "request_sequence": usage_record.request_sequence,
                },
            )
            self.events.append_model_response_received_event(run, request, result)
            self.events.append_model_provider_used_event(run, request)
            audit.record_provider_used(
                run,
                request,
                job,
                fallback_selected=fallback_selected,
            )
            routing.record_success(run, request.model_provider_credential_id)
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
