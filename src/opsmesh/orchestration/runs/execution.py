from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
    AgentRunResult,
)
from opsmesh.agents.execution.errors import AgentExecutionPolicyError
from opsmesh.agents.execution.state import AgentResumeStateStore
from opsmesh.agents.providers.contracts import ModelProviderUnavailableError
from opsmesh.identity.authorization.resource_queries import (
    unbind_resource_queries,
)
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.approvals.agent_tool_interruptions import (
    AgentToolInterruptionService,
)
from opsmesh.orchestration.approvals.pending_tools import (
    PendingToolInvocationService,
)
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.runs.authorization.policy import RunRuntimeAuthorizationError
from opsmesh.orchestration.runs.authorization.validation import RunAuthorizationService
from opsmesh.orchestration.runs.events import RunEventRecorder
from opsmesh.orchestration.runs.lifecycle import RunLifecycleService
from opsmesh.orchestration.runs.lifecycle_messages import RunLifecycleMessageWriter
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.shared.config import Settings, get_settings

TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


@dataclass(slots=True)
class RunExecutionService:
    session: Session
    lifecycle: RunLifecycleService
    settings: Settings | None = None

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()

    def reject_authorization(self, run: AgentRun, exc: ResourceAccessDeniedError) -> AgentRun:
        # Discard uncommitted output and terminate through trusted lifecycle code. Revoked
        # visibility must not prevent releasing reservations or settling the owning task.
        unbind_resource_queries(self.session)
        self.session.rollback()
        self.session.refresh(run)
        if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            return run
        self._events().append_event(
            run,
            "authorization.revoked",
            "Execution principal no longer authorized",
            {},
        )
        self.lifecycle.mark_run_failed(run, exc)
        self.commit_and_refresh(run)
        return run

    def prepare_run(self, run: AgentRun, job: ExecutionRequest) -> AgentRunRequest | None:
        if self._run_should_skip_execution(run):
            self.commit_and_refresh(run)
            return None

        self._events().append_run_claimed_event(run, job)
        self.lifecycle.mark_run_started(run)
        self.session.commit()

        try:
            request = self._request_builder().build_agent_request(run, job)
        except RunRuntimeAuthorizationError as exc:
            RunAuthorizationService(self.session).record_runtime_denial(run, exc)
            self.lifecycle.mark_run_failed(
                run,
                AgentExecutionPolicyError(
                    code=exc.code,
                    message=str(exc),
                    event_type="runtime.authorization_blocked",
                    metadata={"reason": exc.code},
                ),
            )
            self.commit_and_refresh(run)
            return None
        except (ModelProviderUnavailableError, AgentExecutionPolicyError) as exc:
            if isinstance(exc, ModelProviderUnavailableError):
                self._events().append_model_provider_unavailable_event(run, exc)
            else:
                self._events().append_event(run, exc.event_type, exc.message, exc.metadata)
            self.lifecycle.mark_run_failed(run, exc)
            self.commit_and_refresh(run)
            return None

        if request is not None:
            self._events().append_context_built_event(run, request)
        return request

    def complete_cancellation(self, run: AgentRun, *, provider: str | None) -> AgentRun:
        unbind_resource_queries(self.session)
        self.session.refresh(run)
        if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            return run
        self.lifecycle.mark_run_cancelled(run, completed_at=datetime.now(UTC))
        self._events().append_event(
            run,
            "run.cancellation_propagated",
            "Cancellation stopped the active runtime operation",
            {"provider": provider} if provider is not None else {},
        )
        self.commit_and_refresh(run)
        return run

    def persist_model_result(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: ExecutionRequest,
        result: AgentRunResult,
    ) -> AgentRun:
        if self.run_cancelled_after_model_result(run):
            self.commit_and_refresh(run)
            return run
        if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            return run
        if request.approval_decisions:
            PendingToolInvocationService(
                self.session,
                self._request_builder().secret_service(),
            ).mark_decisions_consumed(
                workspace_id=run.workspace_id,
                run_id=run.id,
                decisions=request.approval_decisions,
            )
        RunLifecycleMessageWriter(self.session).map(run, result)
        if result.resume_state is not None:
            self._state_store().save(
                workspace_id=run.workspace_id,
                run_id=run.id,
                state=result.resume_state,
            )
            AgentToolInterruptionService(
                self.session,
                self._request_builder().secret_service(),
            ).persist(
                context=request.context,
                requested_by_agent_profile_id=run.agent_profile_id,
                interruptions=result.interruptions,
            )
            self.lifecycle.mark_run_waiting_approval(run)
            self.commit_and_refresh(run)
            return run
        if request.resume_state is not None:
            self._state_store().mark_consumed(workspace_id=run.workspace_id, run_id=run.id)
        self.lifecycle.mark_run_completed(run, result, job.requested_by_user_id)
        self.commit_and_refresh(run)
        return run

    def _run_should_skip_execution(self, run: AgentRun) -> bool:
        self.session.refresh(run)
        status = RunStatus(run.status)
        if status == RunStatus.CANCELLED:
            return True
        if status in TERMINAL_RUN_STATUSES:
            return True
        if not self._linked_task_cancelled(run):
            return False
        self.lifecycle.mark_run_cancelled(run, completed_at=datetime.now(UTC))
        self._events().append_event(
            run,
            "run.skipped_cancelled",
            "Run was skipped because the linked task was already cancelled",
        )
        return True

    def run_cancelled_after_model_result(self, run: AgentRun) -> bool:
        self.session.refresh(run)
        status = RunStatus(run.status)
        if status == RunStatus.CANCELLED:
            self._events().append_event(
                run,
                "run.result_discarded_after_cancel",
                "Model result was discarded because the run was cancelled",
            )
            return True
        if status in TERMINAL_RUN_STATUSES:
            return False
        if not self._linked_task_cancelled(run):
            return False
        self.lifecycle.mark_run_cancelled(run, completed_at=datetime.now(UTC))
        self._events().append_event(
            run,
            "run.result_discarded_after_cancel",
            "Model result was discarded because the linked task was cancelled",
        )
        return True

    def _linked_task_cancelled(self, run: AgentRun) -> bool:
        if run.task_id is None:
            return False
        task = self.session.get(Task, run.task_id)
        return task is not None and TaskStatus(task.status) == TaskStatus.CANCELLED

    def _request_builder(self) -> RunRequestBuilder:
        return RunRequestBuilder(
            self.session,
            self._settings(),
        )

    def _events(self) -> RunEventRecorder:
        return RunEventRecorder(self.session)

    def _state_store(self) -> AgentResumeStateStore:
        return AgentResumeStateStore(
            self.session,
            self._request_builder().secret_service(),
        )

    def commit_and_refresh(self, run: AgentRun) -> None:
        self.session.commit()
        self.session.refresh(run)

    def _settings(self) -> Settings:
        if self.settings is None:
            raise ValueError("Run execution settings are not configured")
        return self.settings
