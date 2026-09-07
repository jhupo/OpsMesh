import asyncio
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.agent_runtime.factory import build_agent_runner
from backend.app.core.config import Settings, get_settings
from backend.app.model_providers.service_models import ModelProviderUnavailableError
from backend.app.orchestration.model_run_gateway import ModelRunGateway
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_lifecycle import RunLifecycleService
from backend.app.orchestration.run_request_builder import RunRequestBuilder
from backend.app.orchestration.run_runtime_event_messages import RunRuntimeEventMessageMapper
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.redis_queue import RedisQueue

TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


@dataclass(slots=True)
class RunExecutionDependencies:
    lifecycle: RunLifecycleService


@dataclass(slots=True)
class RunExecutionService:
    session: Session
    dependencies: RunExecutionDependencies
    queue: RedisQueue | None = None
    agent_runner: AgentRunner | None = None
    settings: Settings | None = None
    docker_client: DockerRuntimeClient | None = None

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self.agent_runner = self.agent_runner or build_agent_runner(self.settings)

    async def run_agent(self, job: JobPayload) -> AgentRun:
        run = self.session.get(AgentRun, job.resource_id)
        if run is None:
            raise ValueError("Agent run not found")
        if run.workspace_id != job.workspace_id:
            raise ValueError("Agent run workspace mismatch")

        with self._lock_for_run(run) as acquired:
            if not acquired:
                raise RuntimeError("Agent run is already locked")

            if self._run_cancelled_before_execution(run):
                self._commit_and_refresh(run)
                return run

            self._events().append_run_claimed_event(run, job)
            self._lifecycle().mark_run_started(run)
            self.session.commit()

            try:
                request = self._request_builder().build_agent_request(run, job)
            except ModelProviderUnavailableError as exc:
                self._events().append_model_provider_unavailable_event(run, exc)
                self._lifecycle().mark_run_failed(run, exc)
                self._commit_and_refresh(run)
                return run

            self._events().append_context_built_event(run, request)
            result = await self._model_gateway().run_with_provider_fallback(run, request, job)
            if result is None:
                self._commit_and_refresh(run)
                return run

            if self._run_cancelled_after_model_result(run):
                self._commit_and_refresh(run)
                return run

            RunRuntimeEventMessageMapper(self.session).map(run, result)
            if self._agent_result_waiting_runtime(result) or self._run_has_waiting_runtime_event(
                run
            ):
                self._lifecycle().mark_run_waiting_runtime(run)
                self._commit_and_refresh(run)
                return run

            self._lifecycle().mark_run_completed(run, result, job.requested_by_user_id)
            self._commit_and_refresh(run)
            return run

    def run_agent_sync(self, job: JobPayload) -> AgentRun:
        return asyncio.run(self.run_agent(job))

    def _run_cancelled_before_execution(self, run: AgentRun) -> bool:
        self.session.refresh(run)
        status = RunStatus(run.status)
        if status == RunStatus.CANCELLED:
            return True
        if status in TERMINAL_RUN_STATUSES:
            return False
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

    def _model_gateway(self) -> ModelRunGateway:
        return ModelRunGateway(
            session=self.session,
            settings=self._settings(),
            agent_runner=self._agent_runner(),
            request_builder=self._request_builder(),
            events=self._events(),
            mark_run_failed=self._lifecycle().mark_run_failed,
        )

    def _request_builder(self) -> RunRequestBuilder:
        return RunRequestBuilder(self.session, self._settings(), self.docker_client)

    def _events(self) -> RunEventRecorder:
        return RunEventRecorder(self.session)

    def _lifecycle(self) -> RunLifecycleService:
        return self.dependencies.lifecycle

    def _agent_result_waiting_runtime(self, result: object) -> bool:
        return self._lifecycle().agent_result_waiting_runtime(result)

    def _lock_for_run(self, run: AgentRun) -> AbstractContextManager[bool]:
        if self.queue is not None:
            return self.queue.run_lock(str(run.workspace_id), str(run.id))
        return _NoopLock()

    def _commit_and_refresh(self, run: AgentRun) -> None:
        self.session.commit()
        self.session.refresh(run)

    def _settings(self) -> Settings:
        if self.settings is None:
            raise ValueError("Run execution settings are not configured")
        return self.settings

    def _agent_runner(self) -> AgentRunner:
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
