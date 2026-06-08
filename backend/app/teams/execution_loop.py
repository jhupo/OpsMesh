from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError
from backend.app.runtime_manager.safety import RuntimeSafetyError
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TERMINAL_TASK_STATUSES, TaskStatus
from backend.app.teams.command_center import TeamCommandCenterService
from backend.app.teams.models import AgentTeam
from backend.app.teams.provider_readiness import TeamProviderReadinessService
from backend.app.teams.runtime import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
    TeamRuntimeService,
)
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace

COMPLETED_STEP_STATUSES = {"completed", "cancelled", "canceled"}
ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}
TEAM_EXECUTION_LOOP_WINDOW_SECONDS = 60
TEAM_RUNTIME_DEFAULT_LOOP_INTERVAL_SECONDS = 300


@dataclass(frozen=True)
class TeamExecutionLoopEnqueueSummary:
    enqueued: int
    skipped: int
    skipped_reasons: dict[str, int] = field(default_factory=dict)


class TeamExecutionLoopService:
    """Advance team tasks when execution, handoff, and PM acceptance are complete."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_status(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        max_finalize_tasks: int = 50,
    ) -> dict[str, object] | None:
        if not self._team_exists(workspace_id=workspace_id, team_id=team_id):
            return None

        command_center = TeamCommandCenterService(self._session).get_command_center(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
        )
        if command_center is None:
            return None

        finalization = self.finalize_ready_tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=None,
            dry_run=True,
            max_tasks=max_finalize_tasks,
        )
        if finalization is None:
            return None
        command_center = _without_finalizable_review_actions(command_center, finalization)

        command_center_summary = (
            command_center.get("summary")
            if isinstance(command_center.get("summary"), dict)
            else {}
        )
        summary = {
            "team_status": command_center_summary.get("team_status"),
            "delivery_health": command_center_summary.get("delivery_health"),
            "action_plan_count": _int_from(command_center_summary, "action_plan_count"),
            "needs_attention_tasks": _int_from(command_center_summary, "needs_attention_tasks"),
            "finalizable_task_count": len(_finalizable_task_ids(finalization)),
            "scanned_task_count": _int_from(finalization, "scanned_task_count"),
            "queue_truncated": bool(command_center_summary.get("queue_truncated")),
        }
        if summary["action_plan_count"] > 0:
            status = "needs_attention"
        elif summary["finalizable_task_count"] > 0:
            status = "ready_to_finalize"
        else:
            status = "idle"

        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "status": status,
            "summary": summary,
            "command_center": command_center,
            "finalization": finalization,
        }

    def run_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        dry_run: bool = True,
        apply_command_center_actions: bool = True,
        enqueue_runs: bool = True,
        finalize_ready_tasks: bool = True,
        include_completed: bool = False,
        queue_limit: int = 50,
        sources: list[str] | None = None,
        actions: list[str] | None = None,
        max_actions: int = 5,
        max_tasks_per_action: int = 100,
        max_finalize_tasks: int = 50,
        queue: RedisQueue | None = None,
        runtime_control: RuntimeControlService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        team = self._team(workspace_id=workspace_id, team_id=team_id)
        if team is None:
            return None
        runtime_status = _runtime_status(team)
        if runtime_status in {TEAM_RUNTIME_PAUSED, TEAM_RUNTIME_STOPPED}:
            summary = {
                "runtime_status": runtime_status,
                "reason": "team_runtime_not_running",
            }
            response = {
                "workspace_id": workspace_id,
                "team_id": team_id,
                "generated_at": datetime.now(UTC),
                "dry_run": dry_run,
                "status": "skipped",
                "summary": summary,
                "command_center_actions": None,
                "finalization": None,
            }
            if not dry_run:
                self._record_iteration(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    status="skipped",
                    summary=summary,
                )
            return response
        if not dry_run:
            runtime_service = TeamRuntimeService(self._session)
            if runtime_status != TEAM_RUNTIME_RUNNING:
                runtime_service.start(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    reason="execution_loop_started_runtime",
                    metadata=metadata,
                )
            else:
                runtime_service.get_state(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    initialize=True,
                )
            if runtime_control is not None:
                try:
                    runtime_service.ensure_workspace_runtime(
                        workspace_id=workspace_id,
                        team_id=team_id,
                        actor_user_id=actor_user_id,
                        runtime_control=runtime_control,
                        start=True,
                        reason="execution_loop_ensured_runtime",
                        metadata=metadata,
                    )
                except (RuntimeQuotaExceededError, RuntimeSafetyError, ValueError) as exc:
                    summary = {
                        "runtime_status": runtime_status or TEAM_RUNTIME_RUNNING,
                        "reason": "team_workspace_runtime_unavailable",
                        "error": str(exc),
                    }
                    self._record_iteration(
                        workspace_id=workspace_id,
                        team_id=team_id,
                        actor_user_id=actor_user_id,
                        status="skipped",
                        summary=summary,
                    )
                    return {
                        "workspace_id": workspace_id,
                        "team_id": team_id,
                        "generated_at": datetime.now(UTC),
                        "dry_run": dry_run,
                        "status": "skipped",
                        "summary": summary,
                        "command_center_actions": None,
                        "finalization": None,
                    }

        finalization = None
        if finalize_ready_tasks:
            finalization = self.finalize_ready_tasks(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                dry_run=dry_run,
                max_tasks=max_finalize_tasks,
            )
            if finalization is None:
                return None

        command_center_actions = None
        if apply_command_center_actions:
            command_center_actions = TeamCommandCenterService(self._session).apply_action_plan(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                include_completed=include_completed,
                queue_limit=queue_limit,
                dry_run=dry_run,
                sources=sources,
                actions=actions,
                max_actions=max_actions,
                max_tasks_per_action=max_tasks_per_action,
                enqueue_runs=enqueue_runs,
                queue=queue,
                runtime_control=runtime_control,
                reason=reason,
                metadata=metadata,
            )
            if command_center_actions is None:
                return None

        summary = _iteration_summary(
            command_center_actions=command_center_actions,
            finalization=finalization,
            apply_command_center_actions=apply_command_center_actions,
            finalize_ready_tasks=finalize_ready_tasks,
        )
        if not dry_run:
            self._record_iteration(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                status="advanced" if _advanced(summary) else "noop",
                summary=summary,
            )

        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "advanced" if _advanced(summary) else "noop",
            "summary": summary,
            "command_center_actions": command_center_actions,
            "finalization": finalization,
        }

    def finalize_ready_tasks(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID | None,
        dry_run: bool = True,
        max_tasks: int = 50,
    ) -> dict[str, object] | None:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return None

        tasks = self._tasks(workspace_id=workspace_id, team_id=team_id, limit=max_tasks)
        results = [
            self._finalization_result(task=task, actor_user_id=actor_user_id, dry_run=dry_run)
            for task in tasks
        ]
        finalized = [item for item in results if item["status"] == "finalized"]
        if finalized and not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.execution_loop.tasks_finalized",
                target_type="agent_team",
                target_id=team_id,
                metadata={
                    "finalized_task_ids": [str(item["task_id"]) for item in finalized],
                    "scanned_task_count": len(tasks),
                },
            )
        if not dry_run:
            self._session.commit()

        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "finalized" if finalized else "noop",
            "scanned_task_count": len(tasks),
            "finalized_task_count": len(finalized),
            "skipped_task_count": len(results) - len(finalized),
            "results": results,
        }

    def _team(self, *, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )

    def _team_exists(self, *, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == team_id,
                )
            )
            is not None
        )

    def _record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        TeamRuntimeService(self._session).record_iteration(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="team.execution_loop.iteration_ran",
            target_type="agent_team",
            target_id=team_id,
            metadata=summary,
        )
        self._session.commit()

    def _tasks(self, *, workspace_id: UUID, team_id: UUID, limit: int) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .where(
                    Task.workspace_id == workspace_id,
                    Task.agent_team_id == team_id,
                    ~Task.status.in_([status.value for status in TERMINAL_TASK_STATUSES]),
                )
                .order_by(Task.priority.desc(), Task.updated_at.desc(), Task.id.asc())
                .limit(limit)
            )
        )

    def _finalization_result(
        self,
        *,
        task: Task,
        actor_user_id: UUID | None,
        dry_run: bool,
    ) -> dict[str, object]:
        blocked_reason = self._blocked_reason(task)
        if blocked_reason is not None:
            return _result(task, "skipped", blocked_reason)

        approval = self._latest_approved_acceptance(task)
        if approval is None:
            return _result(task, "skipped", "approved_acceptance_missing")

        final_output = _final_output_from_acceptance(approval)
        if dry_run:
            return _result(task, "would_finalize", "ready", final_output=final_output)

        TaskStateService().transition(
            task,
            TaskStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            final_output=final_output,
        )
        AuditService(self._session).record_user_action(
            workspace_id=task.workspace_id,
            user_id=actor_user_id,
            action="task.execution_loop.finalized",
            target_type="task",
            target_id=task.id,
            metadata={
                "team_id": str(task.agent_team_id),
                "acceptance_message_id": str(approval.id),
            },
        )
        return _result(task, "finalized", "ready", final_output=final_output)

    def _blocked_reason(self, task: Task) -> str | None:
        if task.status != TaskStatus.RUNNING.value:
            return "task_status_not_finalizable"
        if self._has_incomplete_steps(task):
            return "team_steps_incomplete"
        if self._has_active_runs(task):
            return "active_runs_present"
        diagnostics = TaskManagerDiagnosticsService(self._session).get_diagnostics(
            workspace_id=task.workspace_id,
            task_id=task.id,
        )
        summary = (
            diagnostics.get("summary")
            if isinstance(diagnostics, dict) and isinstance(diagnostics.get("summary"), dict)
            else {}
        )
        if summary.get("status") != "healthy":
            return "manager_acceptance_not_healthy"
        return None

    def _has_incomplete_steps(self, task: Task) -> bool:
        count = self._session.scalar(
            select(TaskStep.id)
            .where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                ~TaskStep.status.in_(COMPLETED_STEP_STATUSES),
            )
            .limit(1)
        )
        return count is not None

    def _has_active_runs(self, task: Task) -> bool:
        count = self._session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.workspace_id == task.workspace_id,
                AgentRun.task_id == task.id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )
        return count is not None

    def _latest_approved_acceptance(self, task: Task) -> TaskMessage | None:
        messages = self._session.scalars(
            select(TaskMessage)
            .where(
                TaskMessage.workspace_id == task.workspace_id,
                TaskMessage.task_id == task.id,
                TaskMessage.message_type == "pm.acceptance_decision",
            )
            .order_by(TaskMessage.sequence.desc(), TaskMessage.created_at.desc())
        ).all()
        for message in messages:
            payload = message.payload if isinstance(message.payload, dict) else {}
            if payload.get("decision") == "approved":
                return message
        return None


class TeamExecutionLoopQueueService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_active_team_iterations(
        self,
        *,
        queue: RedisQueue,
        limit: int = 50,
        now: datetime | None = None,
    ) -> TeamExecutionLoopEnqueueSummary:
        window = int((now or datetime.now(UTC)).timestamp() // TEAM_EXECUTION_LOOP_WINDOW_SECONDS)
        generated_at = now or datetime.now(UTC)
        task_teams = self._session.scalars(
            select(Task)
            .where(
                Task.agent_team_id.is_not(None),
                Task.created_by_user_id.is_not(None),
                ~Task.status.in_([status.value for status in TERMINAL_TASK_STATUSES]),
            )
            .order_by(Task.priority.desc(), Task.updated_at.desc(), Task.id.asc())
            .limit(max(limit, 1) * 5)
        ).all()
        runtime_status = AgentTeam.default_task_policy[TEAM_RUNTIME_STATUS_KEY][
            "status"
        ].as_string()
        runtime_teams = self._session.scalars(
            select(AgentTeam)
            .where(
                AgentTeam.status == "active",
                or_(
                    runtime_status.in_(
                        [
                            TEAM_RUNTIME_RUNNING,
                            TEAM_RUNTIME_PAUSED,
                            TEAM_RUNTIME_STOPPED,
                        ]
                    ),
                    AgentTeam.default_task_policy[TEAM_RUNTIME_STATUS_KEY][
                        "last_iteration"
                    ].is_not(None),
                ),
            )
            .order_by(AgentTeam.updated_at.desc(), AgentTeam.id.asc())
            .limit(max(limit, 1) * 5)
        ).all()
        candidates: list[dict[str, object]] = [
            _team_loop_candidate(
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
                requested_by_user_id=task.created_by_user_id,
                priority=task.priority,
                trigger="active_team_task",
                task_id=task.id,
            )
            for task in task_teams
            if task.agent_team_id is not None and task.created_by_user_id is not None
        ]
        skipped_reasons: dict[str, int] = {}
        skipped = 0
        for team in runtime_teams:
            runtime_candidate, skip_reason = self._runtime_candidate(
                team,
                generated_at=generated_at,
            )
            if runtime_candidate is None:
                if skip_reason is not None:
                    skipped += 1
                    _increment_skip_reason(skipped_reasons, skip_reason)
                    _record_runtime_scheduler_scan(
                        team,
                        status="skipped",
                        reason=skip_reason,
                        scanned_at=generated_at,
                        window=window,
                    )
                continue
            if skip_reason is not None and _runtime_candidate_is_skip(runtime_candidate):
                skipped += 1
                _increment_skip_reason(skipped_reasons, skip_reason)
                _record_runtime_scheduler_scan(
                    team,
                    status="skipped",
                    reason=skip_reason,
                    scanned_at=generated_at,
                    window=window,
                    runtime_candidate=runtime_candidate,
                )
                continue
            owner_user_id = self._workspace_owner_id(team.workspace_id)
            if owner_user_id is None:
                skipped += 1
                _increment_skip_reason(skipped_reasons, "workspace_owner_missing")
                _record_runtime_scheduler_scan(
                    team,
                    status="skipped",
                    reason="workspace_owner_missing",
                    scanned_at=generated_at,
                    window=window,
                    runtime_candidate=runtime_candidate,
                )
                continue
            candidates.append(
                _team_loop_candidate(
                    workspace_id=team.workspace_id,
                    team_id=team.id,
                    requested_by_user_id=owner_user_id,
                    priority=runtime_candidate["priority"],
                    trigger=runtime_candidate["trigger"],
                    task_id=None,
                    routing={
                        "runtime_health": runtime_candidate["runtime_health"],
                        "workspace_runtime_id": runtime_candidate["workspace_runtime_id"],
                        "last_heartbeat_at": runtime_candidate["last_heartbeat_at"],
                    },
                )
            )
        seen: set[tuple[UUID, UUID]] = set()
        enqueued = 0
        for candidate in candidates:
            team_key = (candidate["workspace_id"], candidate["team_id"])
            if team_key in seen:
                continue
            seen.add(team_key)
            accepted = enqueue_team_execution_loop_job(
                queue=queue,
                workspace_id=candidate["workspace_id"],
                team_id=candidate["team_id"],
                requested_by_user_id=candidate["requested_by_user_id"],
                idempotency_suffix=f"maintenance:{window}",
                priority=candidate["priority"],
                routing={
                    "source": "worker_maintenance",
                    "trigger": candidate["trigger"],
                    **dict(candidate.get("routing") or {}),
                    "task_id": (
                        str(candidate["task_id"])
                        if candidate["task_id"] is not None
                        else None
                    ),
                },
            )
            if accepted:
                enqueued += 1
                if candidate["task_id"] is None:
                    team = self._session.get(AgentTeam, candidate["team_id"])
                    if team is not None:
                        _record_runtime_scheduler_scan(
                            team,
                            status="enqueued",
                            reason=None,
                            scanned_at=generated_at,
                            window=window,
                            runtime_candidate=_scheduler_scan_candidate(candidate),
                        )
            else:
                skipped += 1
                _increment_skip_reason(skipped_reasons, "queue_idempotency_duplicate")
                if candidate["task_id"] is None:
                    team = self._session.get(AgentTeam, candidate["team_id"])
                    if team is not None:
                        _record_runtime_scheduler_scan(
                            team,
                            status="skipped",
                            reason="queue_idempotency_duplicate",
                            scanned_at=generated_at,
                            window=window,
                            runtime_candidate=_scheduler_scan_candidate(candidate),
                        )
            if enqueued >= limit:
                break
        return TeamExecutionLoopEnqueueSummary(
            enqueued=enqueued,
            skipped=skipped,
            skipped_reasons=skipped_reasons,
        )

    def _workspace_owner_id(self, workspace_id: UUID) -> UUID | None:
        return self._session.scalar(
            select(Workspace.owner_user_id).where(Workspace.id == workspace_id)
        )

    def _runtime_candidate(
        self,
        team: AgentTeam,
        *,
        generated_at: datetime,
    ) -> tuple[dict[str, object] | None, str | None]:
        policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
        runtime_metadata = policy.get(TEAM_RUNTIME_STATUS_KEY)
        if not isinstance(runtime_metadata, dict):
            return None, None
        runtime_status = runtime_metadata.get("status")
        if runtime_status == TEAM_RUNTIME_PAUSED:
            return None, "team_runtime_paused"
        if runtime_status == TEAM_RUNTIME_STOPPED:
            return None, "team_runtime_stopped"
        if runtime_status != TEAM_RUNTIME_RUNNING:
            return None, None

        provider_readiness = TeamProviderReadinessService(self._session).get_readiness(
            workspace_id=team.workspace_id,
            team_id=team.id,
        )
        if _provider_readiness_blocks_runtime(provider_readiness):
            return _provider_blocked_runtime_candidate(provider_readiness), (
                "provider_readiness_blocked"
            )

        workspace_runtime_id = _uuid_or_none(
            runtime_metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY)
        )
        workspace_runtime = (
            self._workspace_runtime(team.workspace_id, workspace_runtime_id)
            if workspace_runtime_id is not None
            else None
        )
        last_heartbeat_at = _datetime_or_none(runtime_metadata.get("last_heartbeat_at"))
        runtime_health = _runtime_candidate_health(
            runtime_metadata=runtime_metadata,
            workspace_runtime=workspace_runtime,
            workspace_runtime_id=workspace_runtime_id,
            last_heartbeat_at=last_heartbeat_at,
            generated_at=generated_at,
        )
        if runtime_health == "healthy":
            scheduled_candidate = _scheduled_runtime_candidate(
                runtime_metadata=runtime_metadata,
                generated_at=generated_at,
            )
            if scheduled_candidate is None:
                return None, "scheduled_team_runtime_not_due"
            priority = scheduled_candidate["priority"]
            trigger = "scheduled_team_runtime"
        if runtime_health == "degraded":
            priority = 20
            trigger = "degraded_team_runtime"
        elif runtime_health == "stale":
            priority = 15
            trigger = "stale_team_runtime"
        elif runtime_health != "healthy":
            priority = 0
            trigger = "running_team_runtime"
        return {
            "priority": priority,
            "trigger": trigger,
            "runtime_health": runtime_health,
            "workspace_runtime_id": str(workspace_runtime_id)
            if workspace_runtime_id is not None
            else None,
            "last_heartbeat_at": last_heartbeat_at.isoformat()
            if last_heartbeat_at is not None
            else None,
        }, None

    def _workspace_runtime(
        self,
        workspace_id: UUID,
        workspace_runtime_id: UUID,
    ) -> WorkspaceRuntime | None:
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == workspace_runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
        )


def enqueue_team_execution_loop_job(
    *,
    queue: RedisQueue,
    workspace_id: UUID,
    team_id: UUID,
    requested_by_user_id: UUID | None,
    idempotency_suffix: str,
    priority: int = 0,
    routing: dict[str, object] | None = None,
) -> bool:
    return queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.TEAM_EXECUTION_LOOP,
            resource_id=team_id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=(
                f"team.execution_loop:{workspace_id}:{team_id}:{idempotency_suffix}"
            ),
            priority=priority,
            routing=routing or {},
        )
    )


def _result(
    task: Task,
    status: str,
    reason: str,
    *,
    final_output: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "task_id": task.id,
        "previous_status": task.status,
        "status": status,
        "reason": reason,
        "final_output": final_output,
    }


def _iteration_summary(
    *,
    command_center_actions: dict[str, object] | None,
    finalization: dict[str, object] | None,
    apply_command_center_actions: bool,
    finalize_ready_tasks: bool,
) -> dict[str, object]:
    return {
        "apply_command_center_actions": apply_command_center_actions,
        "finalize_ready_tasks": finalize_ready_tasks,
        "eligible_action_count": _int_from(command_center_actions, "eligible_action_count"),
        "applied_action_count": _int_from(command_center_actions, "applied_action_count"),
        "scheduled_run_count": _int_from(command_center_actions, "scheduled_run_count"),
        "scheduled_run_skip_reason": _string_from(
            command_center_actions,
            "scheduled_run_skip_reason",
        ),
        "scanned_task_count": _int_from(finalization, "scanned_task_count"),
        "finalized_task_count": _int_from(finalization, "finalized_task_count"),
        "skipped_task_count": _int_from(finalization, "skipped_task_count"),
    }


def _advanced(summary: dict[str, object]) -> bool:
    return any(
        _int(summary.get(key)) > 0
        for key in ("applied_action_count", "scheduled_run_count", "finalized_task_count")
    )


def _int_from(payload: dict[str, object] | None, key: str) -> int:
    if payload is None:
        return 0
    return _int(payload.get(key))


def _string_from(payload: dict[str, object] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _without_finalizable_review_actions(
    command_center: dict[str, object],
    finalization: dict[str, object],
) -> dict[str, object]:
    finalizable_task_ids = _finalizable_task_ids(finalization)
    if not finalizable_task_ids:
        return command_center

    action_plan = _dict_list(command_center.get("action_plan"))
    filtered_actions: list[dict[str, object]] = []
    suppressed = 0
    for item in action_plan:
        action = item.get("action")
        task_ids = _object_list(item.get("task_ids"))
        if action != "request_manager_review" or not task_ids:
            filtered_actions.append(item)
            continue

        kept_task_ids = [
            task_id for task_id in task_ids if str(task_id) not in finalizable_task_ids
        ]
        if not kept_task_ids and not _object_list(item.get("task_step_ids")):
            suppressed += 1
            continue

        adjusted = {**item, "task_ids": kept_task_ids}
        if isinstance(adjusted.get("count"), int):
            adjusted["count"] = min(int(adjusted["count"]), len(kept_task_ids))
        filtered_actions.append(adjusted)

    if suppressed == 0 and len(filtered_actions) == len(action_plan):
        return command_center

    summary = (
        dict(command_center["summary"])
        if isinstance(command_center.get("summary"), dict)
        else {}
    )
    summary["action_plan_count"] = len(filtered_actions)
    summary["action_plan_source_counts"] = _action_source_counts(filtered_actions)
    summary["suppressed_finalization_action_count"] = suppressed
    return {
        **command_center,
        "summary": summary,
        "action_plan": filtered_actions,
    }


def _finalizable_task_ids(finalization: dict[str, object]) -> set[str]:
    return {
        str(item["task_id"])
        for item in _dict_list(finalization.get("results"))
        if item.get("status") in {"would_finalize", "finalized"} and item.get("task_id")
    }


def _action_source_counts(action_plan: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in action_plan:
        source = item.get("source")
        if isinstance(source, str) and source:
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _final_output_from_acceptance(message: TaskMessage) -> dict[str, object]:
    payload = message.payload if isinstance(message.payload, dict) else {}
    summary = payload.get("summary")
    return {
        "summary": summary if isinstance(summary, str) and summary else "Approved",
        "source": "pm_acceptance_decision",
        "acceptance_message_id": str(message.id),
        "decision": "approved",
    }


def _runtime_status(team: AgentTeam) -> str | None:
    policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
    runtime = policy.get(TEAM_RUNTIME_STATUS_KEY)
    if not isinstance(runtime, dict):
        return None
    status = runtime.get("status")
    return status if isinstance(status, str) else None


def _runtime_candidate_health(
    *,
    runtime_metadata: dict[str, object],
    workspace_runtime: WorkspaceRuntime | None,
    workspace_runtime_id: UUID | None,
    last_heartbeat_at: datetime | None,
    generated_at: datetime,
) -> str:
    if workspace_runtime_id is not None and workspace_runtime is None:
        return "degraded"
    if workspace_runtime is not None and (
        workspace_runtime.status != "running"
        or workspace_runtime.connection_status in {"offline", "error"}
    ):
        return "degraded"
    if runtime_metadata.get("heartbeat_status") == "skipped":
        return "degraded"
    if last_heartbeat_at is None:
        return "starting"
    if generated_at - last_heartbeat_at > timedelta(
        seconds=TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS
    ):
        return "stale"
    return "healthy"


def _scheduled_runtime_candidate(
    *,
    runtime_metadata: dict[str, object],
    generated_at: datetime,
) -> dict[str, object] | None:
    scheduling_policy = _runtime_scheduling_policy(runtime_metadata)
    if scheduling_policy.get("scheduled_loop_enabled") is False:
        return None
    loop_interval_seconds = _positive_int(
        scheduling_policy.get("loop_interval_seconds"),
        TEAM_RUNTIME_DEFAULT_LOOP_INTERVAL_SECONDS,
    )
    last_iteration_at = _last_iteration_recorded_at(runtime_metadata) or _datetime_or_none(
        runtime_metadata.get("last_heartbeat_at")
    )
    if last_iteration_at is not None and generated_at - last_iteration_at < timedelta(
        seconds=loop_interval_seconds
    ):
        return None
    return {
        "priority": _positive_int(scheduling_policy.get("priority"), 5),
    }


def _runtime_scheduling_policy(runtime_metadata: dict[str, object]) -> dict[str, object]:
    nested = runtime_metadata.get("scheduling_policy")
    if isinstance(nested, dict):
        return dict(nested)
    return {
        key: runtime_metadata[key]
        for key in ("scheduled_loop_enabled", "loop_interval_seconds", "priority")
        if key in runtime_metadata
    }


def _last_iteration_recorded_at(runtime_metadata: dict[str, object]) -> datetime | None:
    last_iteration = runtime_metadata.get("last_iteration")
    if not isinstance(last_iteration, dict):
        return None
    return _datetime_or_none(last_iteration.get("recorded_at"))


def _record_runtime_scheduler_scan(
    team: AgentTeam,
    *,
    status: str,
    reason: str | None,
    scanned_at: datetime,
    window: int,
    runtime_candidate: dict[str, object] | None = None,
) -> None:
    policy = dict(team.default_task_policy or {})
    runtime_metadata = dict(policy.get(TEAM_RUNTIME_STATUS_KEY) or {})
    scan: dict[str, object] = {
        "status": status,
        "scanned_at": scanned_at.isoformat(),
        "window": window,
    }
    if reason:
        scan["reason"] = reason
    if runtime_candidate:
        for key in ("trigger", "runtime_health", "workspace_runtime_id", "last_heartbeat_at"):
            value = runtime_candidate.get(key)
            if isinstance(value, str) and value:
                scan[key] = value
        provider_readiness = runtime_candidate.get("provider_readiness")
        if isinstance(provider_readiness, dict):
            scan["provider_readiness"] = provider_readiness
    runtime_metadata["last_scheduler_scan"] = scan
    policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
    team.default_task_policy = policy


def _scheduler_scan_candidate(candidate: dict[str, object]) -> dict[str, object]:
    return {
        "trigger": candidate.get("trigger"),
        **dict(candidate.get("routing") or {}),
    }


def _runtime_candidate_is_skip(candidate: dict[str, object]) -> bool:
    return candidate.get("skip") is True


def _provider_readiness_blocks_runtime(provider_readiness: dict[str, object]) -> bool:
    value = provider_readiness.get("runtime_blocked_member_count")
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _provider_blocked_runtime_candidate(
    provider_readiness: dict[str, object],
) -> dict[str, object]:
    return {
        "skip": True,
        "trigger": "scheduled_team_runtime",
        "runtime_health": "provider_blocked",
        "provider_readiness": {
            "status": provider_readiness.get("status"),
            "runtime_blocked_member_count": provider_readiness.get(
                "runtime_blocked_member_count"
            ),
            "runtime_blocking_reasons": provider_readiness.get(
                "runtime_blocking_reasons"
            )
            or {},
        },
    }


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _increment_skip_reason(skipped_reasons: dict[str, int], reason: str) -> None:
    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1


def _datetime_or_none(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _uuid_or_none(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _team_loop_candidate(
    *,
    workspace_id: UUID,
    team_id: UUID,
    requested_by_user_id: UUID,
    priority: int,
    trigger: str,
    task_id: UUID | None,
    routing: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "team_id": team_id,
        "requested_by_user_id": requested_by_user_id,
        "priority": priority,
        "trigger": trigger,
        "task_id": task_id,
        "routing": routing or {},
    }
