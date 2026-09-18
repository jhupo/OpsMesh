import asyncio
import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.utils import payload_hash
from backend.app.domains.access.execution import ExecutionIdentityService
from backend.app.domains.access.resource_queries import (
    execution_resource_queries,
    unbind_resource_queries,
)
from backend.app.domains.access.resources import ResourceAccessDenied
from backend.app.domains.agents.providers.contracts import ModelProviderUnavailableError
from backend.app.domains.agents.runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeContext,
    AgentRuntimeExecutor,
    AgentRuntimeStructuredOutput,
    AgentRuntimeToolExecutor,
    AgentRuntimeToolResult,
)
from backend.app.domains.agents.runtime.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimePolicyError,
)
from backend.app.domains.agents.runtime.state import AgentRunStateStore
from backend.app.domains.agents.runtime.tools.gateway import AgentToolGateway
from backend.app.domains.orchestration.approvals.agent_tool_interruptions import (
    AgentToolInterruptionService,
)
from backend.app.domains.orchestration.approvals.models import Approval
from backend.app.domains.orchestration.approvals.pending_tools import (
    PendingToolInvocationRequest,
    PendingToolInvocationService,
)
from backend.app.domains.orchestration.approvals.service import ApprovalService
from backend.app.domains.orchestration.approvals.waiting import ApprovalWaitingService
from backend.app.domains.orchestration.requests.builder import RunRequestBuilder
from backend.app.domains.orchestration.requests.run_gateway import ModelRunGateway
from backend.app.domains.orchestration.runs.authorization.policy import (
    RunRuntimeAuthorizationError,
)
from backend.app.domains.orchestration.runs.authorization.validation import RunAuthorizationService
from backend.app.domains.orchestration.runs.events import RunEventRecorder
from backend.app.domains.orchestration.runs.lifecycle import RunLifecycleService
from backend.app.domains.orchestration.runs.live_events import LiveToolExecutor, RunLivePublisher
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.runs.runtime_event_messages import (
    RunRuntimeEventMessageMapper,
)
from backend.app.domains.orchestration.runs.state import RunStatus
from backend.app.domains.orchestration.tasks.events import RedisTaskEventBus
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.orchestration.tasks.state import TaskStatus
from backend.app.domains.orchestration.workflows.definitions.data import resolve_workflow_inputs
from backend.app.domains.orchestration.workflows.definitions.subworkflows import (
    SubworkflowExecutionService,
)
from backend.app.domains.workspace.projects.io.service import RunProjectIOService
from backend.app.domains.workspace.projects.io.support import ProjectRunIOError
from backend.app.domains.workspace.storage.storage import ObjectStorage
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.contracts import RuntimeEnvironmentError
from backend.app.runtime.environment.backends.registry import RuntimeBackendRegistry
from backend.app.runtime.environment.contracts import DockerRuntimeClient
from backend.app.runtime.environment.run_environment import RunRuntimeEnvironmentService
from backend.app.runtime.workers.contracts import JobPayload
from backend.app.runtime.workers.queue import RedisQueue

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
    agent_runner: AgentRuntimeExecutor | None = None
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

    async def run_agent(self, job: JobPayload) -> AgentRun:
        run = self.session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == job.workspace_id,
                AgentRun.id == job.resource_id,
            )
        )
        if run is None:
            raise ValueError("Agent run not found")
        if run.workspace_id != job.workspace_id:
            raise ValueError("Agent run workspace mismatch")
        if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            return run

        try:
            user = ExecutionIdentityService(self.session).for_run(run.workspace_id, run.id)
            with execution_resource_queries(self.session, run.workspace_id, user):
                return await self._run_authorized_agent(
                    run, job.model_copy(update={"requested_by_user_id": user.user_id}),
                )
        except ResourceAccessDenied as exc:
            return self._reject_authorization(run, exc)

    def _reject_authorization(self, run: AgentRun, exc: ResourceAccessDenied) -> AgentRun:
        # Discard uncommitted output and terminate through trusted lifecycle code. Revoked
        # visibility must not prevent releasing reservations or settling the owning task.
        unbind_resource_queries(self.session)
        self.session.rollback()
        self.session.refresh(run)
        if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            return run
        self._events().append_event(
            run, "authorization.revoked", "Execution principal no longer authorized", {},
        )
        self._lifecycle().mark_run_failed(run, exc)
        self._commit_and_refresh(run)
        return run

    async def _run_authorized_agent(self, run: AgentRun, job: JobPayload) -> AgentRun:

        with self._lock_for_run(run) as acquired:
            if not acquired:
                raise RuntimeError("Agent run is already locked")

            if self._run_should_skip_execution(run):
                self._commit_and_refresh(run)
                return run

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
                self._commit_and_refresh(run)
                return run

            node_type = self._node_type(run)
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
                self._commit_and_refresh(run)
                return run
            except (ModelProviderUnavailableError, AgentRuntimePolicyError) as exc:
                if isinstance(exc, ModelProviderUnavailableError):
                    self._events().append_model_provider_unavailable_event(run, exc)
                else:
                    self._events().append_event(run, exc.event_type, exc.message, exc.metadata)
                self._lifecycle().mark_run_failed(run, exc)
                self._commit_and_refresh(run)
                return run

            if request is not None:
                self._events().append_context_built_event(run, request)
            try:
                direct_result = await self._run_non_agent_node(run, job, request, node_type)
            except Exception as exc:
                self._lifecycle().mark_run_failed(run, exc)
                self._commit_and_refresh(run)
                return run
            if direct_result is not None:
                return self._complete_direct_result(run, direct_result, job)
            assert request is not None
            return await self._execute_model_result(run, request, job)

    def _complete_direct_result(
        self,
        run: AgentRun,
        result: AgentRuntimeToolResult,
        job: JobPayload,
    ) -> AgentRun:
        ExecutionIdentityService(self.session).for_run(run.workspace_id, run.id)
        if result.status == "waiting_approval":
            self._lifecycle().mark_run_waiting_approval(run)
        elif result.status == "waiting_subworkflow":
            self._lifecycle().mark_run_waiting_subworkflow(run)
        elif result.status == "completed":
            output = result.output or {}
            self._lifecycle().mark_run_completed(
                run,
                AgentRunResult(
                    final_output=json.dumps(output, ensure_ascii=False, default=str),
                    raw_output=output,
                    structured_output=AgentRuntimeStructuredOutput(
                        value=output,
                        schema_name="direct_tool_result",
                        validated=False,
                    ),
                ),
                job.requested_by_user_id,
            )
        else:
            self._lifecycle().mark_run_failed(
                run,
                ValueError(str((result.error or {}).get("message", "Tool failed"))),
            )
        if result.status in {"completed", "failed"} and self._node_type(run) in {"tool", "mcp"}:
            PendingToolInvocationService(
                self.session,
                self._request_builder().secret_service(),
            ).mark_decisions_consumed(workspace_id=run.workspace_id, run_id=run.id)
        self._commit_and_refresh(run)
        return run

    async def _execute_model_result(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
    ) -> AgentRun:
        try:
            result = await self._run_model_with_runtime_limit(run, request, job)
        except TimeoutError:
            return self._complete_timeout(run)
        except AgentRuntimeCancelledError:
            return self._complete_cancellation(run, request)
        ExecutionIdentityService(self.session).for_run(run.workspace_id, run.id)
        if result is None or self._run_cancelled_after_model_result(run):
            self._commit_and_refresh(run)
            return run
        return self._persist_model_result(run, request, job, result)

    def _complete_timeout(self, run: AgentRun) -> AgentRun:
        unbind_resource_queries(self.session)
        timeout_seconds = self._runtime_timeout_seconds(run)
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
        self._commit_and_refresh(run)
        return run

    def _complete_cancellation(self, run: AgentRun, request: AgentRunRequest) -> AgentRun:
        unbind_resource_queries(self.session)
        self.session.refresh(run)
        if RunStatus(run.status) not in TERMINAL_RUN_STATUSES:
            self._lifecycle().mark_run_cancelled(run, completed_at=datetime.now(UTC))
        self._events().append_event(
            run,
            "run.cancellation_propagated",
            "Cancellation stopped the active agent SDK run",
            {"provider": request.provider},
        )
        self._commit_and_refresh(run)
        return run

    def _persist_model_result(
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
            self._commit_and_refresh(run)
            return run
        if self._lifecycle().agent_result_waiting_runtime(
            result
        ) or self._run_has_waiting_runtime_event(run):
            self._lifecycle().mark_run_waiting_runtime(run)
            self._commit_and_refresh(run)
            return run
        if request.resume_state is not None:
            self._state_store().mark_consumed(workspace_id=run.workspace_id, run_id=run.id)
        try:
            self._project_io().harvest_outputs(run, actor_user_id=job.requested_by_user_id)
        except ProjectRunIOError as exc:
            self._lifecycle().mark_run_failed(run, exc)
            self._commit_and_refresh(run)
            return run
        self._lifecycle().mark_run_completed(run, result, job.requested_by_user_id)
        self._commit_and_refresh(run)
        return run

    def run_agent_sync(self, job: JobPayload) -> AgentRun:
        return asyncio.run(self.run_agent(job))

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

    def _run_cancelled_after_model_result(self, run: AgentRun) -> bool:
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

    async def _run_model_with_runtime_limit(
        self,
        run: AgentRun,
        request: AgentRunRequest,
        job: JobPayload,
    ) -> AgentRunResult | None:
        timeout_seconds = self._runtime_timeout_seconds(run)
        gateway = self._model_gateway().run_with_provider_fallback(run, request, job)
        if timeout_seconds is None:
            return await gateway
        return await asyncio.wait_for(gateway, timeout=timeout_seconds)

    def _runtime_timeout_seconds(self, run: AgentRun) -> int | None:
        if run.runtime_id is None:
            return None
        from backend.app.runtime.environment.models import WorkspaceRuntime

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

    async def _run_non_agent_node(
        self,
        run: AgentRun,
        job: JobPayload,
        request: AgentRunRequest | None,
        node_type: str | None,
    ) -> AgentRuntimeToolResult | None:
        if run.task_step_id is None:
            return None
        from backend.app.domains.orchestration.tasks.models import TaskStep

        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or not isinstance(step.dependencies, dict):
            return None
        raw_node_type = step.dependencies.get("node_type")
        node_type = node_type or (raw_node_type if isinstance(raw_node_type, str) else None)
        if node_type == "approval":
            approval = ApprovalService(self.session).create_approval(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                agent_run_id=run.id,
                requested_by_agent_profile_id=run.agent_profile_id,
                approval_type="workflow.node",
                risk_level="medium",
                payload={
                    "node_type": "approval",
                    "work_package_id": step.work_package_id,
                    "title": step.title,
                    "acceptance_criteria": step.acceptance_criteria,
                },
            )
            ApprovalWaitingService(self.session).mark_waiting(
                workspace_id=run.workspace_id, run_id=run.id, task_id=run.task_id
            )
            return AgentRuntimeToolResult(
                status="waiting_approval",
                metadata={"approval_id": str(approval.id), "node_type": "approval"},
            )
        if node_type == "subworkflow":
            if run.task_id is None:
                raise ValueError("Subworkflow run has no parent task")
            parent_task = self.session.scalar(
                select(Task).where(
                    Task.workspace_id == run.workspace_id,
                    Task.id == run.task_id,
                )
            )
            if parent_task is None:
                raise ValueError("Subworkflow parent task not found")
            launch = SubworkflowExecutionService(self.session, queue=self.queue).launch(
                parent_run=run,
                parent_task=parent_task,
                parent_step=step,
                requested_by_user_id=job.requested_by_user_id,
            )
            return AgentRuntimeToolResult(
                status=launch.status,
                output=launch.output,
                error=launch.error,
                metadata={
                    "invocation_id": str(launch.invocation_id),
                    "child_task_id": str(launch.child_task_id),
                    "child_run_id": str(launch.child_run_id)
                    if launch.child_run_id is not None
                    else None,
                    "node_type": "subworkflow",
                },
            )
        if node_type not in {"tool", "mcp"}:
            return None
        tool_name = step.dependencies.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("Direct tool node has no tool name")
        arguments = step.dependencies.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Direct tool node arguments must be an object")
        if run.task_id is None:
            raise ValueError("Direct tool run has no parent task")
        parent_task = self.session.scalar(
            select(Task).where(
                Task.workspace_id == run.workspace_id,
                Task.id == run.task_id,
            )
        )
        if parent_task is None:
            raise ValueError("Direct tool parent task not found")
        arguments = {
            **{key: value for key, value in arguments.items() if isinstance(key, str)},
            **resolve_workflow_inputs(self.session, parent_task, step),
        }
        context: AgentRuntimeContext
        tool_executor: AgentRuntimeToolExecutor | None
        if request is None:
            context, tool_executor = self._request_builder().build_direct_tool_context(run, job)
        else:
            tool_executor = request.tool_executor
            context = request.context
        if tool_executor is None:
            raise ValueError("Direct tool node has no authorized tool executor")
        direct_approval = self._direct_tool_approval(run, tool_name)
        approval_status = direct_approval.status if direct_approval is not None else None
        tool_call_id = f"direct:{run.task_step_id}:{tool_name}"
        approved_arguments = (
            self._approved_direct_arguments(context, tool_name, arguments, direct_approval)
            if direct_approval is not None
            else None
        )
        if approval_status == "pending" and direct_approval is not None:
            assert approved_arguments is not None
            self._bind_direct_approval(
                run,
                step.id,
                tool_name,
                node_type,
                approved_arguments,
                tool_call_id,
                direct_approval,
            )
            return AgentRuntimeToolResult(
                status="waiting_approval",
                metadata={"node_type": node_type, "tool_name": tool_name},
            )
        if approval_status == "rejected":
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": "tool_approval_rejected",
                    "message": "Direct tool execution was rejected",
                },
                metadata={"node_type": node_type, "tool_name": tool_name},
            )
        if approval_status == "approved" and direct_approval is not None:
            assert approved_arguments is not None
            self._bind_direct_approval(
                run,
                step.id,
                tool_name,
                node_type,
                approved_arguments,
                tool_call_id,
                direct_approval,
            )
        if self.queue is not None and run.task_id is not None:
            tool_executor = LiveToolExecutor(
                tool_executor,
                RunLivePublisher(
                    RedisTaskEventBus(self.queue.redis, self._settings().redis_key_prefix),
                    run.workspace_id,
                    run.task_id,
                    run.id,
                    step.id,
                    step.work_package_id,
                ),
            )
        result = await tool_executor.execute_tool(
            context=context,
            tool_name=tool_name,
            arguments=arguments,
            tool_call_id=tool_call_id,
            approval_granted=approval_status == "approved",
        )
        if result.status == "waiting_approval":
            direct_approval = self._direct_tool_approval(run, tool_name)
            if direct_approval is None or direct_approval.status != "pending":
                raise ValueError("Direct tool approval was not persisted")
            approved_arguments = self._approved_direct_arguments(
                context, tool_name, arguments, direct_approval
            )
            self._bind_direct_approval(
                run,
                step.id,
                tool_name,
                node_type,
                approved_arguments,
                tool_call_id,
                direct_approval,
            )
        return result

    def _approved_direct_arguments(
        self,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        approval: Approval,
    ) -> dict[str, object]:
        prepared = AgentToolGateway(self.session).prepare(
            context=context,
            tool_name=tool_name,
            arguments=arguments,
        )
        payload = approval.payload if isinstance(approval.payload, dict) else {}
        if payload.get("arguments_sha256") != payload_hash(prepared.arguments):
            raise ValueError("Direct tool arguments changed after approval was requested")
        return prepared.arguments

    def _bind_direct_approval(
        self,
        run: AgentRun,
        step_id: UUID,
        tool_name: str,
        node_type: str,
        arguments: dict[str, object],
        tool_call_id: str,
        approval: Approval,
    ) -> None:
        pending = PendingToolInvocationService(
            self.session,
            self._request_builder().secret_service(),
        )
        invocation = pending.create_or_get(
            PendingToolInvocationRequest(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                agent_run_id=run.id,
                approval_id=approval.id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_kind=node_type,
                arguments=arguments,
                policy_decision={"source": "workflow.direct_tool"},
                idempotency_key=f"direct:{run.id}:{step_id}",
            )
        )
        if approval.status == "approved" and invocation.status == "pending":
            pending.record_decision(
                workspace_id=run.workspace_id,
                approval_id=approval.id,
                status="approved",
            )

    def _direct_tool_approval(self, run: AgentRun, tool_name: str) -> Approval | None:
        approvals = self.session.scalars(
            select(Approval)
            .where(
                Approval.workspace_id == run.workspace_id,
                Approval.agent_run_id == run.id,
                Approval.approval_type.in_(("product.tool", "mcp.tool")),
            )
            .order_by(Approval.created_at.desc(), Approval.id.desc())
        )
        has_prior_tool_approval = False
        for approval in approvals:
            has_prior_tool_approval = True
            payload = approval.payload
            if isinstance(payload, dict) and payload.get("tool_name") == tool_name:
                return approval
        if has_prior_tool_approval:
            raise ValueError("Direct tool changed after approval was requested")
        return None

    def _node_type(self, run: AgentRun) -> str | None:
        if run.task_step_id is None:
            return None
        from backend.app.domains.orchestration.tasks.models import TaskStep

        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or not isinstance(step.dependencies, dict):
            return None
        value = step.dependencies.get("node_type")
        return value if isinstance(value, str) else None

    def _model_gateway(self) -> ModelRunGateway:
        return ModelRunGateway(
            session=self.session,
            settings=self._settings(),
            agent_runner=self._agent_runner(),
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

    def _lock_for_run(self, run: AgentRun) -> AbstractContextManager[bool]:
        if self.queue is not None:
            return self.queue.run_lock(str(run.workspace_id), str(run.id))
        return _NoopLock()

    def _commit_and_refresh(self, run: AgentRun) -> None:
        if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            self._project_io().cleanup_runtime_workspace(run, reason="run_terminal")
            self._runtime_environment().cleanup_for_run(run)
        self.session.commit()
        self.session.refresh(run)

    def _settings(self) -> Settings:
        if self.settings is None:
            raise ValueError("Run execution settings are not configured")
        return self.settings

    def _agent_runner(self) -> AgentRuntimeExecutor:
        if self.agent_runner is None:
            raise ValueError("Run execution agent runner is not configured")
        return self.agent_runner


class _NoopLock:
    def __enter__(self) -> bool:
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None
