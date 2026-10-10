from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import (
    AgentRunRequest,
    AgentRunResult,
)
from backend.app.agents.execution.errors import AgentRuntimePolicyError
from backend.app.agents.execution.state import AgentRunStateStore
from backend.app.agents.providers.contracts import ModelProviderUnavailableError
from backend.app.governance.audit.service import AuditService
from backend.app.identity.authorization.resource_queries import (
    unbind_resource_queries,
)
from backend.app.identity.authorization.resources import ResourceAccessDenied
from backend.app.orchestration.approvals.agent_tool_interruptions import (
    AgentToolInterruptionService,
)
from backend.app.orchestration.approvals.pending_tools import (
    PendingToolInvocationService,
)
from backend.app.orchestration.requests.builder import RunRequestBuilder
from backend.app.orchestration.requests.run_gateway import ModelRunGateway
from backend.app.orchestration.runs.authorization.policy import RunRuntimeAuthorizationError
from backend.app.orchestration.runs.authorization.validation import RunAuthorizationService
from backend.app.orchestration.runs.events import RunEventRecorder
from backend.app.orchestration.runs.lifecycle import RunLifecycleService
from backend.app.orchestration.runs.models import AgentRun, RunEvent
from backend.app.orchestration.runs.runtime_event_messages import RunRuntimeEventMessageMapper
from backend.app.orchestration.runs.state import RunStatus
from backend.app.orchestration.tasks.events import RedisTaskEventBus
from backend.app.orchestration.tasks.models import Task
from backend.app.orchestration.tasks.state import TaskStatus
from backend.app.resources.storage.storage import ObjectStorage
from backend.app.runtime.backends.registry import RuntimeBackendRegistry
from backend.app.runtime.contracts import RuntimeEnvironmentError
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.instances.run_environment import RunRuntimeEnvironmentService
from backend.app.runtime.queues.contracts import JobPayload
from backend.app.runtime.queues.execution_control import current_execution_control
from backend.app.runtime.queues.service import RedisQueue
from backend.app.shared.config import Settings, get_settings
from backend.app.workspaces.projects.io.service import RunProjectIOService
from backend.app.workspaces.projects.io.support import ProjectRunIOError

TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


@dataclass(slots=True)
class RunExecutionDependencies:
    lifecycle: RunLifecycleService
    runtime_backends: RuntimeBackendRegistry


@dataclass(slots=True)
class RunExecutionService:
    session: Session
    dependencies: RunExecutionDependencies
    queue: RedisQueue | None = None
    settings: Settings | None = None
    docker_client: DockerRuntimeClient | None = None
    storage: ObjectStorage | None = None
    _project_io_service: RunProjectIOService | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _runtime_environment_service: RunRuntimeEnvironmentService | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()

    def reject_authorization(self, run: AgentRun, exc: ResourceAccessDenied) -> AgentRun:
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
        self._lifecycle().mark_run_failed(run, exc)
        self.commit_and_refresh(run)
        return run

    def prepare_run(self, run: AgentRun, job: JobPayload) -> AgentRunRequest | None:
        if self._run_should_skip_execution(run):
            self.commit_and_refresh(run)
            return None

        self._events().append_run_claimed_event(run, job)
        self._lifecycle().mark_run_started(run)
        self.session.commit()

        try:
            self._runtime_environment().ensure_for_run(run)
            self._project_io().stage_inputs(
                run,
                actor_user_id=job.requested_by_user_id,
            )
        except (ProjectRunIOError, RuntimeEnvironmentError) as exc:
            self._lifecycle().mark_run_failed(run, exc)
            self.commit_and_refresh(run)
            return None

        node_type = self.node_type(run)
        try:
            request = (
                None
                if node_type in {"tool", "mcp", "approval", "subworkflow"}
                else self._request_builder().build_agent_request(run, job)
            )
        except RunRuntimeAuthorizationError as exc:
            RunAuthorizationService(self.session).record_runtime_denial(run, exc)
            self._lifecycle().mark_run_failed(
                run,
                AgentRuntimePolicyError(
                    code=exc.code,
                    message=str(exc),
                    event_type="runtime.authorization_blocked",
                    metadata={"reason": exc.code},
                ),
            )
            self.commit_and_refresh(run)
            return None
        except (ModelProviderUnavailableError, AgentRuntimePolicyError) as exc:
            if isinstance(exc, ModelProviderUnavailableError):
                self._events().append_model_provider_unavailable_event(run, exc)
            else:
                self._events().append_event(run, exc.event_type, exc.message, exc.metadata)
            self._lifecycle().mark_run_failed(run, exc)
            self.commit_and_refresh(run)
            return None

        if request is not None:
            self._events().append_context_built_event(run, request)
        return request

    def complete_timeout(self, run: AgentRun) -> AgentRun:
        unbind_resource_queries(self.session)
        timeout_seconds = self.runtime_timeout_seconds(run)
        timeout_error = AgentRuntimePolicyError(
            code="runtime_wall_time_exceeded",
            message="Run exceeded the authorized runtime wall-time limit",
            event_type="runtime.limit.exceeded",
            metadata={"limit": "timeout_seconds", "timeout_seconds": timeout_seconds},
            retryable=True,
        )
        self._events().append_event(
            run,
            timeout_error.event_type,
            timeout_error.message,
            timeout_error.metadata,
        )
        AuditService(self.session).record_system_action(
            workspace_id=run.workspace_id,
            action="runtime.limit.exceeded",
            target_type="agent_run",
            target_id=run.id,
            metadata=timeout_error.metadata,
        )
        self._lifecycle().mark_run_failed(run, timeout_error)
        self.commit_and_refresh(run)
        return run

    def complete_cancellation(self, run: AgentRun, *, provider: str | None) -> AgentRun:
        control = current_execution_control()
        if control is not None:
            control.check_ownership()
        unbind_resource_queries(self.session)
        self.session.refresh(run)
        if RunStatus(run.status) not in TERMINAL_RUN_STATUSES:
            self._lifecycle().mark_run_cancelled(run, completed_at=datetime.now(UTC))
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
        job: JobPayload,
        result: AgentRunResult,
    ) -> AgentRun:
        if request.approval_decisions:
            PendingToolInvocationService(
                self.session,
                self._request_builder().secret_service(),
            ).mark_decisions_consumed(workspace_id=run.workspace_id, run_id=run.id)
        RunRuntimeEventMessageMapper(self.session).map(run, result)
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
            self._lifecycle().mark_run_waiting_approval(run)
            self.commit_and_refresh(run)
            return run
        if self._lifecycle().agent_result_waiting_runtime(
            result
        ) or self._run_has_waiting_runtime_event(run):
            self._lifecycle().mark_run_waiting_runtime(run)
            self.commit_and_refresh(run)
            return run
        if request.resume_state is not None:
            self._state_store().mark_consumed(workspace_id=run.workspace_id, run_id=run.id)
        try:
            self._project_io().harvest_outputs(run, actor_user_id=job.requested_by_user_id)
        except ProjectRunIOError as exc:
            self._lifecycle().mark_run_failed(run, exc)
            self.commit_and_refresh(run)
            return run
        self._lifecycle().mark_run_completed(run, result, job.requested_by_user_id)
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
        self._lifecycle().mark_run_cancelled(run, completed_at=datetime.now(UTC))
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
        self._lifecycle().mark_run_cancelled(run, completed_at=datetime.now(UTC))
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

    def runtime_timeout_seconds(self, run: AgentRun) -> int | None:
        if run.runtime_id is None:
            return None
        from backend.app.runtime.instances.models import WorkspaceRuntime

        runtime = self.session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == run.workspace_id,
                WorkspaceRuntime.id == run.runtime_id,
            )
        )
        if runtime is None:
            return None
        value = runtime.limits.get("timeout_seconds")
        if isinstance(value, int) and value > 0:
            return value
        return None

    def _run_has_waiting_runtime_event(self, run: AgentRun) -> bool:
        return (
            self.session.scalar(
                select(RunEvent.id).where(
                    RunEvent.workspace_id == run.workspace_id,
                    RunEvent.agent_run_id == run.id,
                    RunEvent.event_type == "tool.waiting",
                )
            )
            is not None
        )

    def node_type(self, run: AgentRun) -> str | None:
        if run.task_step_id is None:
            return None
        from backend.app.orchestration.tasks.models import TaskStep

        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or not isinstance(step.dependencies, dict):
            return None
        value = step.dependencies.get("node_type")
        return value if isinstance(value, str) else None

    def model_gateway(self) -> ModelRunGateway:
        return ModelRunGateway(
            session=self.session,
            settings=self._settings(),
            request_builder=self._request_builder(),
            events=self._events(),
            mark_run_failed=self._lifecycle().mark_run_failed,
            event_bus=RedisTaskEventBus(self.queue.redis, self._settings().redis_key_prefix)
            if self.queue
            else None,
        )

    def _request_builder(self) -> RunRequestBuilder:
        return RunRequestBuilder(
            self.session,
            self._settings(),
            self.docker_client,
            self.dependencies.runtime_backends,
        )

    def _events(self) -> RunEventRecorder:
        return RunEventRecorder(self.session)

    def _lifecycle(self) -> RunLifecycleService:
        return self.dependencies.lifecycle

    def _state_store(self) -> AgentRunStateStore:
        return AgentRunStateStore(
            self.session,
            self._request_builder().secret_service(),
        )

    def _project_io(self) -> RunProjectIOService:
        if self._project_io_service is None:
            self._project_io_service = RunProjectIOService(
                self.session,
                self.storage,
                self.dependencies.runtime_backends,
                self._settings(),
            )
        return self._project_io_service

    def _runtime_environment(self) -> RunRuntimeEnvironmentService:
        if self._runtime_environment_service is None:
            self._runtime_environment_service = RunRuntimeEnvironmentService(
                self.session,
                self.docker_client,
            )
        return self._runtime_environment_service

    def commit_and_refresh(self, run: AgentRun) -> None:
        control = current_execution_control()
        if control is not None:
            control.check_ownership()
        if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            self._project_io().cleanup_runtime_workspace(run, reason="run_terminal")
            self._runtime_environment().cleanup_for_run(run)
        self.session.commit()
        self.session.refresh(run)

    def _settings(self) -> Settings:
        if self.settings is None:
            raise ValueError("Run execution settings are not configured")
        return self.settings
