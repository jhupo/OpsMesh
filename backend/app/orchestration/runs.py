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
from backend.app.planning.member_matching import MemberMatchingService
from backend.app.planning.project_plans import ProjectPlanningService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus, require_run_transition
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue

STEP_STATUS_QUEUED = "queued"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_CANCELLED = "cancelled"


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
        existing_run = self._existing_active_task_run(task)
        if existing_run is not None:
            return existing_run

        first_team_step = self._create_team_step_plan(task)
        if first_team_step is None:
            run = AgentRun(
                workspace_id=task.workspace_id,
                task_id=task.id,
                status=RunStatus.QUEUED.value,
                input={"task_id": str(task.id), "title": task.title},
            )
            self._session.add(run)
        else:
            run = self._create_run_for_step(task, first_team_step)

        TaskStateService().transition(task, TaskStatus.QUEUED)
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

            self._mark_run_completed(run, result.final_output, job.requested_by_user_id)
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
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_RUNNING
                self._append_event(run, "task_step.started", step.title)

    def _mark_run_completed(
        self,
        run: AgentRun,
        final_output: str,
        requested_by_user_id: UUID | None,
    ) -> None:
        require_run_transition(RunStatus(run.status), RunStatus.COMPLETED)
        run.status = RunStatus.COMPLETED.value
        run.output = {"final_output": final_output}
        run.completed_at = datetime.now(UTC)
        self._append_event(run, "run.completed", "Fake run completed")

        if run.task_id is not None:
            task = self._session.get(Task, run.task_id)
            if task is not None:
                if run.task_step_id is not None:
                    self._mark_step_completed(run, final_output)
                    next_run = self._create_and_enqueue_next_step_run(
                        task,
                        requested_by_user_id=requested_by_user_id,
                    )
                    if next_run is not None:
                        return

                TaskStateService().transition(
                    task,
                    TaskStatus.COMPLETED,
                    completed_at=run.completed_at,
                    final_output=self._final_output_for_task(task, fallback=run.output),
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
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_FAILED

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
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_FAILED

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
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                step.status = STEP_STATUS_CANCELLED

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

        parts = [task.title, task.description]
        if run.task_step_id is not None:
            step = self._session.get(TaskStep, run.task_step_id)
            if step is not None and step.workspace_id == run.workspace_id:
                parts.append(f"Current step: {step.title}\n{step.description}".strip())
                previous_summaries = self._completed_step_summaries(
                    task.id,
                    before=step.order_index,
                )
                if previous_summaries:
                    parts.append("Completed step summaries:\n" + "\n".join(previous_summaries))
        return "\n\n".join(part for part in parts if part).strip()

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

    def _create_team_step_plan(self, task: Task) -> TaskStep | None:
        if task.agent_team_id is None:
            return None
        if self._session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        ):
            return self._next_eligible_step(task.id, task.workspace_id)

        snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else None
        if snapshot is None:
            try:
                snapshot = build_team_snapshot(
                    self._session,
                    workspace_id=task.workspace_id,
                    team_id=task.agent_team_id,
                )
            except ValueError:
                snapshot = None
            else:
                task.team_snapshot = snapshot
                self._session.flush([task])
        if snapshot is not None:
            if task.project_plan is None:
                task.project_plan = ProjectPlanningService(
                    MemberMatchingService(self._session)
                ).create_initial_plan(task)
                self._session.flush([task])
            return self._create_team_step_plan_from_snapshot(task, snapshot)

        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
                AgentTeam.status == "active",
            )
        )
        if team is None:
            return None

        members = self._session.scalars(
            select(AgentTeamMember)
            .where(
                AgentTeamMember.workspace_id == task.workspace_id,
                AgentTeamMember.agent_team_id == team.id,
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.team_role.asc())
        ).all()
        if team.manager_agent_profile_id is None and not members:
            return None

        first_step: TaskStep | None = None
        manager_step: TaskStep | None = None
        if team.manager_agent_profile_id is not None:
            manager_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                assigned_agent_profile_id=team.manager_agent_profile_id,
                work_package_id="manager-planning",
                required_role="project_manager",
                required_skills=["planning", "coordination"],
                expected_artifacts=["project_plan"],
                acceptance_criteria=["The team has a clear execution plan."],
                review_policy={"reviewer": "manager", "mode": "self_review"},
                title="Manager planning",
                description="Clarify the goal, split responsibilities, and prepare the team plan.",
                status=STEP_STATUS_QUEUED,
                order_index=0,
                dependencies={},
            )
            self._session.add(manager_step)
            self._session.flush([manager_step])
            first_step = manager_step

        specialist_steps: list[TaskStep] = []
        manager_dependency = (
            {"after_step_ids": [str(manager_step.id)]} if manager_step is not None else {}
        )
        for index, member in enumerate(members, start=1):
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                assigned_agent_profile_id=member.agent_profile_id,
                work_package_id=f"{member.team_role}-{index}",
                required_role=member.team_role,
                required_skills=[
                    str(skill) for skill in member.skill_weights if isinstance(skill, str)
                ],
                expected_artifacts=["work_summary"],
                acceptance_criteria=["The work package produces a clear result summary."],
                review_policy={"reviewer": "manager", "mode": "manager_review"},
                title=f"{member.team_role} execution",
                description=f"Complete the assigned team role work for {team.name}.",
                status=STEP_STATUS_QUEUED,
                order_index=100 + index,
                dependencies=manager_dependency,
            )
            self._session.add(step)
            specialist_steps.append(step)
            if first_step is None:
                first_step = step
        self._session.flush(specialist_steps)

        if team.manager_agent_profile_id is not None and specialist_steps:
            summary_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                assigned_agent_profile_id=team.manager_agent_profile_id,
                work_package_id="manager-summary",
                required_role="project_manager",
                required_skills=["review", "synthesis"],
                expected_artifacts=["final_delivery"],
                acceptance_criteria=[
                    "The final answer integrates all completed work packages."
                ],
                review_policy={"reviewer": "user", "mode": "final_acceptance"},
                title="Manager summary",
                description=(
                    "Review specialist outputs, reconcile issues, and produce the final answer."
                ),
                status=STEP_STATUS_QUEUED,
                order_index=1_000,
                dependencies={"after_step_ids": [str(step.id) for step in specialist_steps]},
            )
            self._session.add(summary_step)

        self._session.flush()
        return first_step

    def _create_team_step_plan_from_snapshot(
        self,
        task: Task,
        snapshot: dict[str, object],
    ) -> TaskStep | None:
        project_plan = task.project_plan if isinstance(task.project_plan, dict) else None
        if project_plan is not None:
            planned_step = self._create_team_step_plan_from_project_plan(task, project_plan)
            if planned_step is not None:
                return planned_step

        team = snapshot.get("team")
        if not isinstance(team, dict):
            return None
        raw_members = snapshot.get("members", [])
        members = (
            [member for member in raw_members if isinstance(member, dict)]
            if isinstance(raw_members, list)
            else []
        )
        manager_agent_profile_id = _uuid_or_none(team.get("manager_agent_profile_id"))
        if manager_agent_profile_id is None and not members:
            return None

        team_name = str(team.get("name") or "team")
        first_step: TaskStep | None = None
        manager_step: TaskStep | None = None
        if manager_agent_profile_id is not None:
            manager_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                assigned_agent_profile_id=manager_agent_profile_id,
                work_package_id="manager-planning",
                required_role="project_manager",
                required_skills=["planning", "coordination"],
                expected_artifacts=["project_plan"],
                acceptance_criteria=["The team has a clear execution plan."],
                review_policy={"reviewer": "manager", "mode": "self_review"},
                title="Manager planning",
                description="Clarify the goal, split responsibilities, and prepare the team plan.",
                status=STEP_STATUS_QUEUED,
                order_index=0,
                dependencies={},
            )
            self._session.add(manager_step)
            self._session.flush([manager_step])
            first_step = manager_step

        specialist_steps: list[TaskStep] = []
        manager_dependency = (
            {"after_step_ids": [str(manager_step.id)]} if manager_step is not None else {}
        )
        ordered_members = sorted(
            members,
            key=lambda member: (
                _int_or_default(member.get("order_index"), 0),
                str(member.get("team_role") or ""),
            ),
        )
        for index, member in enumerate(ordered_members, start=1):
            agent_profile_id = _uuid_or_none(member.get("agent_profile_id"))
            if agent_profile_id is None:
                continue
            team_role = str(member.get("team_role") or "specialist")
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                assigned_agent_profile_id=agent_profile_id,
                work_package_id=f"{team_role}-{index}",
                required_role=team_role,
                required_skills=_string_list_from_mapping_keys(member.get("skill_weights")),
                expected_artifacts=["work_summary"],
                acceptance_criteria=["The work package produces a clear result summary."],
                review_policy={"reviewer": "manager", "mode": "manager_review"},
                title=f"{team_role} execution",
                description=f"Complete the assigned team role work for {team_name}.",
                status=STEP_STATUS_QUEUED,
                order_index=100 + index,
                dependencies=manager_dependency,
            )
            self._session.add(step)
            specialist_steps.append(step)
            if first_step is None:
                first_step = step
        self._session.flush(specialist_steps)

        if manager_agent_profile_id is not None and specialist_steps:
            summary_step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                assigned_agent_profile_id=manager_agent_profile_id,
                work_package_id="manager-summary",
                required_role="project_manager",
                required_skills=["review", "synthesis"],
                expected_artifacts=["final_delivery"],
                acceptance_criteria=[
                    "The final answer integrates all completed work packages."
                ],
                review_policy={"reviewer": "user", "mode": "final_acceptance"},
                title="Manager summary",
                description=(
                    "Review specialist outputs, reconcile issues, and produce the final answer."
                ),
                status=STEP_STATUS_QUEUED,
                order_index=1_000,
                dependencies={"after_step_ids": [str(step.id) for step in specialist_steps]},
            )
            self._session.add(summary_step)

        self._session.flush()
        return first_step

    def _create_team_step_plan_from_project_plan(
        self,
        task: Task,
        project_plan: dict[str, object],
    ) -> TaskStep | None:
        raw_packages = project_plan.get("work_packages", [])
        if not isinstance(raw_packages, list):
            return None

        created_steps_by_package_id: dict[str, TaskStep] = {}
        first_step: TaskStep | None = None
        for index, package in enumerate(raw_packages):
            if not isinstance(package, dict):
                continue
            package_id = str(package.get("package_id") or f"package-{index}")
            raw_dependencies = package.get("depends_on", [])
            dependencies = (
                [
                    str(dependency)
                    for dependency in raw_dependencies
                    if isinstance(dependency, str)
                ]
                if isinstance(raw_dependencies, list)
                else []
            )
            after_step_ids = [
                str(created_steps_by_package_id[dependency].id)
                for dependency in dependencies
                if dependency in created_steps_by_package_id
            ]
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                assigned_agent_profile_id=_uuid_or_none(package.get("assigned_agent_profile_id")),
                work_package_id=package_id,
                required_role=_optional_string(package.get("required_role")),
                required_skills=_string_list(package.get("required_skills")),
                expected_artifacts=_string_list(package.get("expected_artifacts")),
                acceptance_criteria=_string_list(package.get("acceptance_criteria")),
                review_policy=_dict_or_empty(package.get("review_policy")),
                title=str(package.get("title") or "Work package"),
                description=str(package.get("description") or ""),
                status=STEP_STATUS_QUEUED,
                order_index=index * 100,
                dependencies={
                    "after_step_ids": after_step_ids,
                    "work_package_id": package_id,
                    "required_role": package.get("required_role"),
                    "required_skills": package.get("required_skills", []),
                    "expected_artifacts": package.get("expected_artifacts", []),
                    "acceptance_criteria": package.get("acceptance_criteria", []),
                    "review_policy": package.get("review_policy", {}),
                },
            )
            self._session.add(step)
            self._session.flush([step])
            created_steps_by_package_id[package_id] = step
            if first_step is None and not after_step_ids:
                first_step = step

        self._session.flush()
        return first_step

    def _existing_active_task_run(self, task: Task) -> AgentRun | None:
        return self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
            .order_by(AgentRun.created_at.asc())
        )

    def _create_run_for_step(self, task: Task, step: TaskStep) -> AgentRun:
        profile = (
            self._session.get(AgentProfile, step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id is not None
            else None
        )
        run = AgentRun(
            workspace_id=task.workspace_id,
            task_id=task.id,
            task_step_id=step.id,
            agent_profile_id=step.assigned_agent_profile_id,
            status=RunStatus.QUEUED.value,
            input={
                "task_id": str(task.id),
                "task_step_id": str(step.id),
                "title": task.title,
                "step_title": step.title,
                "team_orchestration": True,
            },
            model=profile.model if profile is not None else None,
        )
        self._session.add(run)
        self._session.flush([run])
        return run

    def _mark_step_completed(self, run: AgentRun, final_output: str) -> None:
        if run.task_step_id is None:
            return
        step = self._session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            return
        step.status = STEP_STATUS_COMPLETED
        step.result_summary = final_output
        self._append_event(run, "task_step.completed", step.title)

    def _create_and_enqueue_next_step_run(
        self,
        task: Task,
        *,
        requested_by_user_id: UUID | None,
    ) -> AgentRun | None:
        next_step = self._next_eligible_step(task.id, task.workspace_id)
        if next_step is None:
            return None

        next_run = self._create_run_for_step(task, next_step)
        self.enqueue_run(next_run, requested_by_user_id)
        return next_run

    def _next_eligible_step(self, task_id: UUID, workspace_id: UUID) -> TaskStep | None:
        queued_steps = self._session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status == STEP_STATUS_QUEUED,
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        for step in queued_steps:
            if self._dependencies_satisfied(step):
                return step
        return None

    def _dependencies_satisfied(self, step: TaskStep) -> bool:
        dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
        raw_step_ids = dependencies.get("after_step_ids", [])
        if not isinstance(raw_step_ids, list) or not raw_step_ids:
            return True

        dependency_ids: list[UUID] = []
        for raw_step_id in raw_step_ids:
            try:
                dependency_ids.append(UUID(str(raw_step_id)))
            except ValueError:
                return False

        incomplete_count = self._session.scalar(
            select(func.count(TaskStep.id)).where(
                TaskStep.workspace_id == step.workspace_id,
                TaskStep.id.in_(dependency_ids),
                TaskStep.status != STEP_STATUS_COMPLETED,
            )
        )
        return int(incomplete_count or 0) == 0

    def _completed_step_summaries(self, task_id: UUID, *, before: int) -> list[str]:
        completed_steps = self._session.scalars(
            select(TaskStep)
            .where(
                TaskStep.task_id == task_id,
                TaskStep.status == STEP_STATUS_COMPLETED,
                TaskStep.order_index < before,
                TaskStep.result_summary.is_not(None),
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        return [
            f"- {step.title}: {step.result_summary}"
            for step in completed_steps
            if step.result_summary
        ]

    def _final_output_for_task(
        self,
        task: Task,
        *,
        fallback: dict[str, object] | None,
    ) -> dict[str, object] | None:
        completed_steps = self._session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.status == STEP_STATUS_COMPLETED,
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        if not completed_steps:
            return fallback

        final_summary = next(
            (
                step.result_summary
                for step in reversed(completed_steps)
                if step.result_summary is not None
            ),
            None,
        )
        return {
            "final_output": final_summary,
            "team_orchestration": {
                "steps": [
                    {
                        "task_step_id": str(step.id),
                        "title": step.title,
                        "status": step.status,
                        "work_package_id": step.work_package_id,
                        "required_role": step.required_role,
                        "agent_profile_id": str(step.assigned_agent_profile_id)
                        if step.assigned_agent_profile_id is not None
                        else None,
                        "result_summary": step.result_summary,
                    }
                    for step in completed_steps
                ],
            },
        }


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


def _uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_list_from_mapping_keys(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [key for key in value if isinstance(key, str)]


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
