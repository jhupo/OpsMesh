from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner, AgentRunRequest, AgentRuntimeContext
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.fake import FakeAgentRunner
from backend.app.agents.models import AgentProfile
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus, require_run_transition
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus, require_task_transition
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


class RunOrchestrationService:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._agent_runner = agent_runner or FakeAgentRunner()

    def create_queued_run_for_task(self, task: Task) -> AgentRun:
        run = AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            status=RunStatus.QUEUED.value,
            input={"task_id": str(task.id), "title": task.title},
        )
        task.status = TaskStatus.QUEUED.value
        self._session.add(run)
        self._session.flush()
        return run

    def enqueue_run(self, run: AgentRun, requested_by_user_id: UUID | None) -> bool:
        if self._queue is None:
            return False

        job = JobPayload(
            workspace_id=run.workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=f"agent.run:{run.workspace_id}:{run.id}",
        )
        return self._queue.enqueue(job)

    async def run_agent(self, job: JobPayload) -> AgentRun:
        run = self._session.get(AgentRun, job.resource_id)
        if run is None:
            raise ValueError("Agent run not found")
        if run.workspace_id != job.workspace_id:
            raise ValueError("Agent run workspace mismatch")

        with self._lock_for_run(run) as acquired:
            if not acquired:
                raise RuntimeError("Agent run is already locked")

            self._mark_run_started(run)
            try:
                result = await self._agent_runner.run(self._build_agent_request(run, job))
            except Exception as exc:
                self._mark_run_failed(run, exc)
                self._session.commit()
                raise

            self._mark_run_completed(run, result.final_output)
            self._session.commit()
            self._session.refresh(run)
            return run

    def run_fake_agent(self, job: JobPayload) -> AgentRun:
        import asyncio

        return asyncio.run(self.run_agent(job))

    def _mark_run_started(self, run: AgentRun) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.RUNNING)
        run.status = RunStatus.RUNNING.value
        run.started_at = datetime.now(UTC)
        self._append_event(run, "run.started", "Fake run started")

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                require_task_transition(TaskStatus(task.status), TaskStatus.RUNNING)
                task.status = TaskStatus.RUNNING.value

    def _mark_run_completed(self, run: AgentRun, final_output: str) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.COMPLETED)
        run.status = RunStatus.COMPLETED.value
        run.output = {"final_output": final_output}
        run.completed_at = datetime.now(UTC)
        self._append_event(run, "run.completed", "Fake run completed")

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                require_task_transition(TaskStatus(task.status), TaskStatus.COMPLETED)
                task.status = TaskStatus.COMPLETED.value
                task.final_output = run.output
                task.completed_at = run.completed_at

    def _mark_run_failed(self, run: AgentRun, exc: Exception) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.FAILED)
        error = normalize_agent_error(exc)
        run.status = RunStatus.FAILED.value
        run.error = error.as_dict()
        run.completed_at = datetime.now(UTC)
        self._append_event(run, "run.failed", error.message)

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                task.status = TaskStatus.FAILED.value
                task.completed_at = run.completed_at

    def _append_event(self, run: AgentRun, event_type: str, message: str) -> RunEvent:
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                    RunEvent.agent_run_id == run.id,
                    RunEvent.workspace_id == run.workspace_id,
                )
            )
            or 0
        ) + 1
        event = RunEvent(
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            event_type=event_type,
            sequence=next_sequence,
            message=message,
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event

    def _build_agent_request(self, run: AgentRun, job: JobPayload) -> AgentRunRequest:
        profile = None
        if run.agent_profile_id is not None:
            profile = self._session.get(AgentProfile, run.agent_profile_id)
        if profile is None:
            profile = AgentProfile(
                workspace_id=run.workspace_id,
                name="Default Agent",
                role="worker",
                instructions="Complete the assigned task.",
                model=run.model or "gpt-4.1",
            )

        return AgentRunRequest(
            agent_profile=profile,
            input_text=self._input_text_for_run(run),
            context=AgentRuntimeContext(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                run_id=run.id,
                user_id=job.requested_by_user_id,
            ),
        )

    def _input_text_for_run(self, run: AgentRun) -> str:
        task = self._session.get(Task, run.task_id) if run.task_id is not None else None
        if task is None:
            return str(run.input)
        return f"{task.title}\n\n{task.description}".strip()

    def _lock_for_run(self, run: AgentRun) -> AbstractContextManager[bool]:
        if self._queue is not None:
            return self._queue.run_lock(str(run.workspace_id), str(run.id))
        return _NoopLock()


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


def build_default_queue(redis_client: Any, settings: Any) -> RedisQueue:
    return RedisQueue(
        redis=redis_client,
        keys=RedisKeyBuilder(settings.redis_key_prefix),
        queue_name=settings.worker_queue_name,
    )
