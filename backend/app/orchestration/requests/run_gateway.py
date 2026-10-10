from collections.abc import Callable
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import (
    AgentRunRequest,
    AgentRunResult,
)
from backend.app.agents.execution.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimeProviderError,
)
from backend.app.governance.costs.service import (
    CostAccountingService,
    CostBudgetDecision,
    CostBudgetExceededError,
)
from backend.app.orchestration.requests.builder import RunRequestBuilder
from backend.app.orchestration.requests.provider_audit import ModelProviderAuditService
from backend.app.orchestration.requests.provider_routing import ModelProviderRoutingService
from backend.app.orchestration.requests.request_approval import ModelRequestApprovalService
from backend.app.orchestration.runs.events import RunEventRecorder
from backend.app.orchestration.runs.live_events import LiveToolExecutor, RunLivePublisher
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.tasks.events import TaskEventBus
from backend.app.orchestration.tasks.models import TaskStep
from backend.app.runtime.queues.contracts import JobPayload
from backend.app.runtime.queues.execution_control import (
    current_execution_control,
)
from backend.app.shared.config import Settings

MarkRunFailed = Callable[[AgentRun, Exception], None]


@dataclass(slots=True)
class ModelRunGateway:
    session: Session
    settings: Settings | None
    request_builder: RunRequestBuilder
    events: RunEventRecorder
    mark_run_failed: MarkRunFailed
    event_bus: TaskEventBus | None = None

    def prepare_model_request(
        self, run: AgentRun, request: AgentRunRequest, *, fallback_selected: bool
    ) -> tuple[AgentRunRequest, CostBudgetDecision]:
        if self.event_bus is not None and run.task_id is not None:
            step = (
                self.session.scalar(
                    select(TaskStep).where(
                        TaskStep.workspace_id == run.workspace_id,
                        TaskStep.task_id == run.task_id,
                        TaskStep.id == run.task_step_id,
                    )
                )
                if run.task_step_id
                else None
            )
            publisher = RunLivePublisher(
                self.event_bus,
                run.workspace_id,
                run.task_id,
                run.id,
                run.task_step_id,
                step.work_package_id if step else None,
                allow_text=not bool(
                    request.guardrails and any(rule.blocking for rule in request.guardrails.output)
                ),
            )
            request = replace(
                request,
                event_sink=publisher,
                tool_executor=LiveToolExecutor(request.tool_executor, publisher)
                if request.tool_executor
                else None,
            )
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
        return request, budget_decision

    def record_model_failure(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
        *,
        fallback_selected: bool,
        budget_decision: CostBudgetDecision,
        error: Exception,
    ) -> None:
        costs = CostAccountingService(self.session)
        routing = self.routing()
        audit = self.audit()
        request_sequence = 1 if fallback_selected else 0
        exc = error
        if isinstance(exc, AgentRuntimeCancelledError):
            control = current_execution_control()
            if control is not None:
                control.check_ownership()
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
            return
        # A database failure in a tool invalidates the transaction. End
        # it before metering/auditing the failure, preserving the cause.
        if isinstance(exc, SQLAlchemyError):
            self.session.rollback()
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

    def record_model_success(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
        result: AgentRunResult,
        *,
        fallback_selected: bool,
        budget_decision: CostBudgetDecision,
    ) -> None:
        costs = CostAccountingService(self.session)
        routing = self.routing()
        audit = self.audit()
        request_sequence = 1 if fallback_selected else 0
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
