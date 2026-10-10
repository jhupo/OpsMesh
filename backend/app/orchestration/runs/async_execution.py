"""Coordinate one Agent Run without retaining a thread or ORM Session during SDK waits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import TypeVar, cast

from opentelemetry.trace import SpanKind
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.execution.cancellation import raise_if_cancelled
from backend.app.agents.execution.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeExecutor,
    AgentRuntimeSession,
    AgentRuntimeToolResult,
    AgentSessionBinding,
)
from backend.app.agents.execution.errors import (
    AgentRuntimeCancelledError,
    AgentRuntimePolicyError,
    AgentRuntimeProviderError,
)
from backend.app.agents.execution.tools.scoped import ScopedToolExecutor
from backend.app.agents.sessions.gateway import AuthorizedSDKSession
from backend.app.governance.costs.service import CostBudgetDecision, CostBudgetExceededError
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resource_queries import execution_resource_queries
from backend.app.identity.authorization.resources import ResourceAccessDenied
from backend.app.orchestration.runs.direct_execution import DirectToolCall, DirectWorkflowExecutor
from backend.app.orchestration.runs.events import RunEventRecorder
from backend.app.orchestration.runs.execution import (
    TERMINAL_RUN_STATUSES,
    RunExecutionDependencies,
    RunExecutionService,
)
from backend.app.orchestration.runs.live_async import buffered_live_events
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.runs.scoped_cancellation import ScopedRunCancellation
from backend.app.orchestration.runs.service import RunOrchestrationService
from backend.app.orchestration.runs.state import RunStatus
from backend.app.platform.settings.policy import operational_configuration
from backend.app.runtime.agent_host.executor import RuntimeAgentExecutor
from backend.app.runtime.backends.factory import build_runtime_backend_registry
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.queues.async_run_lock import async_run_lock
from backend.app.runtime.queues.contracts import JobPayload
from backend.app.runtime.queues.execution_control import (
    ExecutionOwnershipLostError,
    current_execution_control,
)
from backend.app.runtime.queues.service import RedisQueue
from backend.app.shared.concurrency import BlockingIO
from backend.app.shared.config import Settings
from backend.app.shared.db.operations import DatabaseOperations
from backend.app.shared.telemetry.trace_context import current_trace_context, telemetry_span

T = TypeVar("T")
DIRECT_NODES = {"tool", "mcp", "approval", "subworkflow"}


@dataclass(frozen=True)
class PreparedAgentRun:
    request: AgentRunRequest | None
    timeout_seconds: int | None


@dataclass(frozen=True)
class AsyncAgentRunExecutor:
    session_factory: Callable[[], Session]
    queue: RedisQueue
    settings: Settings
    agent_runner: AgentRuntimeExecutor | None = None
    docker_client: DockerRuntimeClient | None = None

    @staticmethod
    def _guard() -> None:
        control = current_execution_control()
        if control is not None:
            control.check_ownership()

    def _service(self, session: Session) -> RunExecutionService:
        orchestration = RunOrchestrationService(session, queue=self.queue)
        return RunExecutionService(
            session=session,
            dependencies=RunExecutionDependencies(
                lifecycle=orchestration._run_lifecycle(),
                runtime_backends=build_runtime_backend_registry(
                    self.docker_client,
                    lambda: operational_configuration(session).file_transfer_timeout_seconds,
                ),
            ),
            queue=self.queue,
            settings=self.settings,
            docker_client=self.docker_client,
        )

    def _run(self, session: Session, job: JobPayload) -> AgentRun:
        run = session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == job.workspace_id, AgentRun.id == job.resource_id
            )
        )
        if run is None:
            raise ValueError("Agent run not found in workspace")
        return run

    async def _phase(
        self,
        database: DatabaseOperations,
        job: JobPayload,
        operation: Callable[[RunExecutionService, AgentRun, JobPayload], T],
    ) -> T:
        def execute(session: Session) -> T:
            run = self._run(session, job)
            user = ExecutionIdentityService(session).for_run(job.workspace_id, run.id)
            with execution_resource_queries(session, job.workspace_id, user):
                return operation(
                    self._service(session),
                    run,
                    job.model_copy(update={"requested_by_user_id": user.user_id}),
                )

        return await database.run(execute)

    def _detach(
        self,
        request: AgentRunRequest,
        database: DatabaseOperations,
        controls: DatabaseOperations,
        job: JobPayload,
    ) -> AgentRunRequest:
        storage = request.session
        if storage is not None and not isinstance(storage, AgentSessionBinding):
            raise TypeError("Run request contains an unsupported persistence adapter")
        return replace(
            request,
            session=cast(
                AgentRuntimeSession, AuthorizedSDKSession(storage, database, job.resource_id)
            )
            if storage is not None
            else None,
            tool_executor=ScopedToolExecutor(database, self.settings, self.docker_client)
            if (
                request.context.allowed_tools
                or request.context.tool_definitions
                or request.agent_tools
            )
            else None,
            cancellation=ScopedRunCancellation(controls, job.workspace_id, job.resource_id),
        )

    async def handle(self, job: JobPayload, io: BlockingIO, control_io: BlockingIO) -> None:
        database = DatabaseOperations(self.session_factory, io, self._guard)
        controls = DatabaseOperations(self.session_factory, control_io, self._guard)
        terminal, direct = await database.run(
            lambda session: (
                RunStatus(self._run(session, job).status) in TERMINAL_RUN_STATUSES,
                self._service(session).node_type(self._run(session, job)) in DIRECT_NODES,
            )
        )
        if terminal:
            return
        async with async_run_lock(
            self.queue, str(job.workspace_id), str(job.resource_id), control_io
        ) as acquired:
            if not acquired:
                raise RuntimeError("Agent run is already locked")
            prepared: PreparedAgentRun | None = None
            try:
                if direct:
                    direct_result = await self._phase(database, job, self._prepare_direct)
                    if isinstance(direct_result, DirectToolCall):
                        call = direct_result
                        tool_result = await ScopedToolExecutor(
                            database, self.settings, self.docker_client
                        ).execute_tool(
                            context=call.context,
                            tool_name=call.tool_name,
                            arguments=call.arguments,
                            tool_call_id=call.tool_call_id,
                            approval_granted=call.approval_granted,
                        )
                        await self._phase(
                            database,
                            job,
                            lambda service, run, authorized_job: DirectWorkflowExecutor(
                                service
                            ).complete_call(run, authorized_job, call, tool_result),
                        )
                    elif direct_result is not None:
                        await self._phase(
                            database,
                            job,
                            lambda service, run, authorized_job: DirectWorkflowExecutor(
                                service
                            )._complete_direct_result(run, direct_result, authorized_job),
                        )
                    return
                prepared = await self._phase(
                    database,
                    job,
                    lambda service, run, authorized_job: PreparedAgentRun(
                        request=self._prepared_request(
                            service, run, authorized_job, database, controls
                        ),
                        timeout_seconds=service.runtime_timeout_seconds(run),
                    ),
                )
                if prepared.request is None:
                    return
                async with asyncio.timeout(prepared.timeout_seconds):
                    result = await self._execute_model(database, controls, job, prepared.request)
                if result is not None:
                    await self._phase(
                        database,
                        job,
                        partial(self._complete, request=prepared.request, result=result),
                    )
            except ExecutionOwnershipLostError:
                raise
            except ResourceAccessDenied as exc:
                await database.run(partial(self._reject, job=job, error=exc))
            except AgentRuntimeCancelledError:
                provider = (
                    prepared.request.provider
                    if prepared is not None and prepared.request is not None
                    else None
                )
                await database.run(partial(self._cancel, job=job, provider=provider))
            except TimeoutError:
                await database.run(partial(self._timeout, job=job))
            except Exception as exc:
                await self._fail(database, job, exc)
                if not isinstance(exc, (CostBudgetExceededError, AgentRuntimePolicyError)):
                    raise
            finally:
                if prepared is not None and prepared.request is not None:
                    native_session = prepared.request.session
                    if isinstance(native_session, AuthorizedSDKSession):
                        await native_session.close()

    def _prepare_direct(
        self, service: RunExecutionService, run: AgentRun, job: JobPayload
    ) -> DirectToolCall | AgentRuntimeToolResult | None:
        service.prepare_run(run, job)
        if run.status in {"completed", "failed", "cancelled", "waiting_runtime"}:
            return None
        return DirectWorkflowExecutor(service).prepare_node(run, job, None, service.node_type(run))

    def _prepared_request(
        self,
        service: RunExecutionService,
        run: AgentRun,
        job: JobPayload,
        database: DatabaseOperations,
        controls: DatabaseOperations,
    ) -> AgentRunRequest | None:
        request = service.prepare_run(run, job)
        return self._detach(request, database, controls, job) if request else None

    async def _execute_model(
        self,
        database: DatabaseOperations,
        controls: DatabaseOperations,
        job: JobPayload,
        original: AgentRunRequest,
    ) -> AgentRunResult | None:
        request = original
        for fallback in (False, True):
            attempt = await self._phase(
                database, job, partial(self._start_attempt, request=request, fallback=fallback)
            )
            if attempt is None:
                return None
            request, budget = attempt
            try:
                runner = self.agent_runner
                if runner is None:
                    if self.docker_client is None:
                        raise ValueError("Runtime SDK transport is not configured")
                    runner = RuntimeAgentExecutor(self.docker_client, controls.io)
                with telemetry_span(
                    "opsmesh.model.request",
                    parent=current_trace_context(),
                    kind=SpanKind.CLIENT,
                    attributes={
                        "opsmesh.workspace.id": str(job.workspace_id),
                        "opsmesh.run.id": str(job.resource_id),
                        "gen_ai.provider.name": request.provider or "openai",
                        "gen_ai.request.model": request.model or request.agent_profile.model,
                        "opsmesh.model.fallback": fallback,
                    },
                ):
                    async with buffered_live_events(request, database.io):
                        result = await runner.run(request)
                control = current_execution_control()
                if control is not None:
                    control.check_ownership()
                await raise_if_cancelled(request.cancellation)
            except ExecutionOwnershipLostError:
                raise
            except Exception as exc:
                await self._phase(
                    database,
                    job,
                    partial(
                        self._record_failure,
                        request=request,
                        fallback=fallback,
                        budget=budget,
                        error=exc,
                    ),
                )
                if fallback or not isinstance(exc, AgentRuntimeProviderError):
                    raise
                selected = await self._phase(
                    database,
                    job,
                    partial(
                        self._select_fallback,
                        request=request,
                        error=exc,
                        database=database,
                        controls=controls,
                    ),
                )
                if selected is None:
                    raise
                request = selected
                continue
            await self._phase(
                database,
                job,
                partial(
                    self._record_success,
                    request=request,
                    result=result,
                    budget=budget,
                    fallback=fallback,
                    original=original,
                ),
            )
            return result
        raise AssertionError("Provider fallback exhausted")

    def _start_attempt(
        self,
        service: RunExecutionService,
        run: AgentRun,
        job: JobPayload,
        *,
        request: AgentRunRequest,
        fallback: bool,
    ) -> tuple[AgentRunRequest, CostBudgetDecision] | None:
        gateway = service.model_gateway()
        if gateway.approvals().requires_approval(run, request):
            if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
                service.commit_and_refresh(run)
            return None
        try:
            return gateway.prepare_model_request(run, request, fallback_selected=fallback)
        except CostBudgetExceededError as error:
            service.dependencies.lifecycle.mark_run_failed(run, error)
            service.commit_and_refresh(run)
            return None

    def _select_fallback(
        self,
        service: RunExecutionService,
        run: AgentRun,
        job: JobPayload,
        *,
        request: AgentRunRequest,
        error: AgentRuntimeProviderError,
        database: DatabaseOperations,
        controls: DatabaseOperations,
    ) -> AgentRunRequest | None:
        selected = (
            service.model_gateway()
            .routing()
            .fallback_request(run=run, job=job, failed_request=request, exc=error)
        )
        if selected is None:
            return None
        if not isinstance(selected.session, AgentSessionBinding) or selected.session.session_id != (
            request.session.session_id if request.session is not None else None
        ):
            raise ValueError("Provider fallback cannot change the authorized SDK session")
        return replace(
            selected,
            session=request.session,
            tool_executor=request.tool_executor,
            cancellation=request.cancellation,
        )

    def _record_failure(
        self,
        service: RunExecutionService,
        run: AgentRun,
        job: JobPayload,
        *,
        request: AgentRunRequest,
        fallback: bool,
        budget: CostBudgetDecision,
        error: Exception,
    ) -> None:
        service.model_gateway().record_model_failure(
            run, request, job, fallback_selected=fallback, budget_decision=budget, error=error
        )

    def _record_success(
        self,
        service: RunExecutionService,
        run: AgentRun,
        job: JobPayload,
        *,
        request: AgentRunRequest,
        result: AgentRunResult,
        budget: CostBudgetDecision,
        fallback: bool,
        original: AgentRunRequest,
    ) -> None:
        service.model_gateway().record_model_success(
            run, request, job, result, fallback_selected=fallback, budget_decision=budget
        )
        if fallback:
            RunEventRecorder(service.session).append_model_provider_fallback_selected_event(
                run, failed_request=original, selected_request=request
            )

    def _complete(
        self,
        service: RunExecutionService,
        run: AgentRun,
        job: JobPayload,
        *,
        request: AgentRunRequest,
        result: AgentRunResult,
    ) -> None:
        if service.run_cancelled_after_model_result(run):
            service.commit_and_refresh(run)
        else:
            service.persist_model_result(run, request, job, result)

    def _reject(self, session: Session, job: JobPayload, error: ResourceAccessDenied) -> None:
        self._service(session).reject_authorization(self._run(session, job), error)

    def _cancel(self, session: Session, job: JobPayload, provider: str | None) -> None:
        self._service(session).complete_cancellation(self._run(session, job), provider=provider)

    def _timeout(self, session: Session, job: JobPayload) -> None:
        self._service(session).complete_timeout(self._run(session, job))

    async def _fail(self, database: DatabaseOperations, job: JobPayload, error: Exception) -> None:
        def operation(session: Session) -> None:
            service, run = self._service(session), self._run(session, job)
            if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
                return
            service.dependencies.lifecycle.mark_run_failed(run, error)
            service.commit_and_refresh(run)

        await database.run(operation)
