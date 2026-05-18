from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner, AgentRunRequest, AgentRuntimeContext
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.fake import FakeAgentRunner
from backend.app.agents.models import AgentProfile
from backend.app.audit.service import AuditService
from backend.app.core.config import Settings
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus, require_run_transition
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


@dataclass(frozen=True)
class StaleRunRecoverySummary:
    recovered_runs: int


class RunOrchestrationService:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        agent_runner: AgentRunner | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._agent_runner = agent_runner or FakeAgentRunner()
        self._settings = settings

    def create_queued_run_for_task(self, task: Task) -> AgentRun:
        run = AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            status=RunStatus.QUEUED.value,
            input={"task_id": str(task.id), "title": task.title},
        )
        TaskStateService().transition(task, TaskStatus.QUEUED)
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

    def cancel_task(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
    ) -> Task | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None
        if TaskStatus(task.status) == TaskStatus.CANCELLED:
            raise ValueError("Task is already cancelled")

        completed_at = datetime.now(UTC)
        TaskStateService().transition(task, TaskStatus.CANCELLED, completed_at=completed_at)

        active_runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
        ).all()
        for run in active_runs:
            self._mark_run_cancelled(run, completed_at=completed_at)

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.cancelled",
            target_type="task",
            target_id=task.id,
            metadata={"title": task.title, "cancelled_runs": len(active_runs)},
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def cancel_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
    ) -> AgentRun | None:
        run = self._session.scalar(
            select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
        )
        if run is None:
            return None

        completed_at = datetime.now(UTC)
        self._mark_run_cancelled(run, completed_at=completed_at)
        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None and TaskStatus(task.status) not in TERMINAL_TASK_STATUSES:
                TaskStateService().transition(
                    task,
                    TaskStatus.CANCELLED,
                    completed_at=completed_at,
                )

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="run.cancelled",
            target_type="agent_run",
            target_id=run.id,
            metadata={"task_id": str(run.task_id) if run.task_id is not None else None},
        )
        self._session.commit()
        self._session.refresh(run)
        return run

    def retry_failed_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_user_id: UUID,
    ) -> AgentRun | None:
        failed_run = self._session.scalar(
            select(AgentRun).where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
        )
        if failed_run is None:
            return None
        if RunStatus(failed_run.status) != RunStatus.FAILED:
            raise ValueError("Only failed runs can be retried")

        task = (
            self._session.get(Task, failed_run.task_id)
            if failed_run.task_id is not None
            else None
        )
        if task is not None:
            TaskStateService().transition(task, TaskStatus.QUEUED)

        retry_run = AgentRun(
            workspace_id=failed_run.workspace_id,
            task_id=failed_run.task_id,
            task_step_id=failed_run.task_step_id,
            agent_profile_id=failed_run.agent_profile_id,
            runtime_id=failed_run.runtime_id,
            status=RunStatus.QUEUED.value,
            input=failed_run.input,
            model=failed_run.model,
        )
        self._session.add(retry_run)
        self._session.flush()
        self._append_event(
            retry_run,
            "run.retry_queued",
            f"Retry queued from failed run {failed_run.id}",
        )
        self.enqueue_run(retry_run, actor_user_id)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="run.retried",
            target_type="agent_run",
            target_id=retry_run.id,
            metadata={
                "failed_run_id": str(failed_run.id),
                "task_id": str(failed_run.task_id) if failed_run.task_id is not None else None,
            },
        )
        self._session.commit()
        self._session.refresh(retry_run)
        return retry_run

    def recover_stale_running_runs(
        self,
        *,
        stale_after_seconds: int,
        limit: int = 100,
    ) -> StaleRunRecoverySummary:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        stale_runs = self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.status == RunStatus.RUNNING.value,
                AgentRun.started_at.is_not(None),
                AgentRun.started_at < cutoff,
            )
            .order_by(AgentRun.started_at.asc())
            .limit(limit)
        ).all()
        for run in stale_runs:
            self._mark_run_recovered_failed(run)
        self._session.commit()
        return StaleRunRecoverySummary(recovered_runs=len(stale_runs))

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
                TaskStateService().transition(task, TaskStatus.RUNNING)

    def _mark_run_completed(self, run: AgentRun, final_output: str) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.COMPLETED)
        run.status = RunStatus.COMPLETED.value
        run.output = {"final_output": final_output}
        run.completed_at = datetime.now(UTC)
        self._append_event(run, "run.completed", "Fake run completed")

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                TaskStateService().transition(
                    task,
                    TaskStatus.COMPLETED,
                    completed_at=run.completed_at,
                    final_output=run.output,
                )

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
                TaskStateService().transition(
                    task,
                    TaskStatus.FAILED,
                    completed_at=run.completed_at,
                )

    def _mark_run_recovered_failed(self, run: AgentRun) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.FAILED)
        run.status = RunStatus.FAILED.value
        run.error = {
            "code": "stale_worker_run",
            "message": "Worker stopped reporting before the run completed",
            "retryable": True,
        }
        run.completed_at = datetime.now(UTC)
        self._append_event(
            run,
            "run.recovered_failed",
            "Marked failed after worker lease expired",
        )

        if run.task_id is None:
            return
        task = self._session.get(Task, run.task_id)
        if task is None or TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
            return
        TaskStateService().transition(task, TaskStatus.FAILED, completed_at=run.completed_at)

    def _mark_run_cancelled(self, run: AgentRun, *, completed_at: datetime) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.CANCELLED)
        run.status = RunStatus.CANCELLED.value
        run.error = {
            "code": "cancelled_by_user",
            "message": "Run was cancelled by a workspace user",
            "retryable": False,
        }
        run.completed_at = completed_at
        self._append_event(run, "run.cancelled", "Run was cancelled by a workspace user")

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
        self._session.flush([event])
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

        allowed_tools = self._allowed_tools_for_profile(profile)
        model_provider = self._model_provider_for_profile(profile)
        return AgentRunRequest(
            agent_profile=profile,
            input_text=self._input_text_for_run(run),
            context=AgentRuntimeContext(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                run_id=run.id,
                user_id=job.requested_by_user_id,
                allowed_tools=allowed_tools,
                metadata={
                    "agent_profile_id": str(profile.id) if profile.id is not None else None,
                    "agent_role": profile.role,
                    "run_model": model_provider["model"],
                    "model_provider_credential_id": str(
                        model_provider["model_provider_credential_id"]
                    )
                    if model_provider["model_provider_credential_id"] is not None
                    else None,
                },
            ),
            model=model_provider["model"],
            base_url=model_provider["base_url"],
            api_key=model_provider["api_key"],
            model_provider_credential_id=model_provider["model_provider_credential_id"],
        )

    def _model_provider_for_profile(self, profile: AgentProfile) -> dict[str, Any]:
        if self._settings is None:
            return {
                "model": profile.model,
                "base_url": None,
                "api_key": None,
                "model_provider_credential_id": None,
            }
        resolved = ModelProviderCredentialService(
            self._session,
            SecretEncryptionService(
                secret=self._settings.credential_encryption_secret,
                key_id=self._settings.credential_encryption_key_id,
            ),
        ).resolve_for_agent(
            workspace_id=profile.workspace_id,
            agent_credential_id=profile.model_provider_credential_id,
            agent_model=profile.model,
        )
        return {
            "model": resolved.model,
            "base_url": resolved.base_url,
            "api_key": resolved.api_key,
            "model_provider_credential_id": resolved.credential_id,
        }

    def _input_text_for_run(self, run: AgentRun) -> str:
        task = self._session.get(Task, run.task_id) if run.task_id is not None else None
        if task is None:
            return str(run.input)
        return f"{task.title}\n\n{task.description}".strip()

    def _allowed_tools_for_profile(self, profile: AgentProfile) -> tuple[str, ...]:
        tool_policy = profile.tool_policy if isinstance(profile.tool_policy, dict) else {}
        raw_tools = tool_policy.get("allowed_tools")
        if raw_tools is None:
            raw_tools = tool_policy.get("mcp_tools")
        if not isinstance(raw_tools, list):
            return ()
        return tuple(tool for tool in raw_tools if isinstance(tool, str))

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
