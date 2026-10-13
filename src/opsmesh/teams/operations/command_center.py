from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.tasks.collaboration.manager_diagnostics import (
    TaskManagerDiagnosticsService,
)
from opsmesh.runtime.instances.service import RuntimeEnvironmentService
from opsmesh.teams.execution.overview import TeamExecutionOverviewService
from opsmesh.teams.execution.overview_repository import TeamExecutionOverviewRepository
from opsmesh.teams.operations.action_plan import (
    _group_applicable_actions,
    _merged_action_plan,
    _provider_action_plan,
    _runtime_action_plan,
)
from opsmesh.teams.operations.actions import TeamCommandCenterActionApplier
from opsmesh.teams.operations.queues import command_center_queues
from opsmesh.teams.operations.views import (
    _dict,
    _list,
    _provider_readiness_blocked,
    _runtime_payload,
    _scheduled_run_payload,
    _string_list,
    _summary,
)
from opsmesh.teams.provider_readiness.service import TeamProviderReadinessService
from opsmesh.teams.sessions.scheduling_blocks import scheduled_run_blocking_summary
from opsmesh.teams.sessions.service import TeamOperationService
from opsmesh.teams.sessions.state_builder import TeamOperationSnapshot


class TeamCommandCenterService:
    """Compose team execution diagnostics into a single operator-facing view."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_command_center(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        runtime_state: TeamOperationSnapshot | None = None,
    ) -> dict[str, object] | None:
        task_limit = max(1, min(queue_limit * 4, 1000))
        tasks = TeamExecutionOverviewRepository(self._session).tasks(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            limit=task_limit + 1,
        )
        task_ids = [task.id for task in tasks[:task_limit]]
        repository = TeamExecutionOverviewRepository(self._session)
        steps = repository.steps(workspace_id, task_ids)
        runs = repository.runs(workspace_id, task_ids)
        manager_diagnostics = TaskManagerDiagnosticsService(self._session).diagnostics_for_tasks(
            workspace_id=workspace_id, tasks=tasks[:task_limit], steps=steps
        )
        overview = TeamExecutionOverviewService(self._session).get_overview(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            task_limit=task_limit,
            tasks=tasks,
            steps=steps,
            runs=runs,
            manager_diagnostics=manager_diagnostics,
        )
        if overview is None:
            return None

        handoff_queue, manager_queue = command_center_queues(
            self._session,
            workspace_id=workspace_id,
            team_id=team_id,
            overview=overview,
            limit=queue_limit,
            manager_diagnostics=manager_diagnostics,
            tasks=tasks[:task_limit],
            steps=steps,
            runs=runs,
        )
        action_plan = _merged_action_plan(
            overview=overview,
            handoff_queue=handoff_queue,
            manager_queue=manager_queue,
        )
        runtime_state = runtime_state or TeamOperationService(
            self._session
        ).get_or_initialize_state(
            workspace_id=workspace_id,
            team_id=team_id,
        )
        if runtime_state is None:
            return None
        provider_readiness = TeamProviderReadinessService(self._session).get_readiness(
            workspace_id=workspace_id,
            team_id=team_id,
        )
        provider_action_plan = _provider_action_plan(provider_readiness)
        runtime_action_plan = _runtime_action_plan(runtime_state)
        action_plan = [*provider_action_plan, *runtime_action_plan, *action_plan]
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "summary": _summary(
                overview=overview,
                handoff_queue=handoff_queue,
                manager_queue=manager_queue,
                runtime_state=runtime_state,
                provider_readiness=provider_readiness,
                action_plan=action_plan,
                queue_limit=queue_limit,
            ),
            "runtime": _runtime_payload(
                runtime_state,
                provider_readiness=provider_readiness,
            ),
            "provider_readiness": provider_readiness,
            "operating_policy": runtime_state.operating_policy,
            "memory_summary": runtime_state.memory_summary,
            "overview": overview,
            "queues": {
                "handoff": handoff_queue,
                "manager": manager_queue,
            },
            "action_plan": action_plan,
        }

    def apply_action_plan(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        dry_run: bool = True,
        sources: list[str] | None = None,
        actions: list[str] | None = None,
        max_actions: int = 5,
        max_tasks_per_action: int = 100,
        enqueue_runs: bool = False,
        runtime_control: RuntimeEnvironmentService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        command_center = self.get_command_center(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
        )
        if command_center is None:
            return None

        action_plan = _list(command_center.get("action_plan"))
        grouped, skipped = _group_applicable_actions(
            action_plan=action_plan,
            sources=sources,
            actions=actions,
            max_actions=max_actions,
        )
        results = (
            [
                {
                    "action": item["action"],
                    "automation": item["automation"],
                    "status": "would_apply",
                    "sources": item["sources"],
                    "task_ids": item["task_ids"],
                    "task_step_ids": item["task_step_ids"],
                    "agent_profile_id": item["agent_profile_id"],
                    "candidate_count": item["candidate_count"],
                    "response": None,
                }
                for item in grouped
            ]
            if dry_run
            else TeamCommandCenterActionApplier(self._session).apply_grouped_actions(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                grouped=grouped,
                max_tasks_per_action=max_tasks_per_action,
                runtime_control=runtime_control,
                reason=reason,
                metadata=metadata or {},
            )
        )
        provider_readiness = _dict(command_center.get("provider_readiness"))
        scheduled_run_skip_reason = (
            "provider_readiness_blocked"
            if _provider_readiness_blocked(provider_readiness)
            else None
        )
        scheduled_runs = (
            RunOrchestrationService(self._session).schedule_team_steps(
                workspace_id=workspace_id,
                team_id=team_id,
                requested_by_user_id=actor_user_id,
            )
            if enqueue_runs and not dry_run and scheduled_run_skip_reason is None
            else []
        )
        scheduled_run_blocking = (
            scheduled_run_blocking_summary(
                self._session,
                workspace_id=workspace_id,
                team_id=team_id,
            )
            if enqueue_runs and not dry_run
            else {"blocked_reasons": {}, "blocked_steps": []}
        )
        applied_action_count = sum(1 for item in results if item["status"] == "applied")
        if not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.command_center.actions_applied",
                target_type="agent_team",
                target_id=team_id,
                metadata={
                    "eligible_action_count": len(grouped),
                    "applied_action_count": applied_action_count,
                    "skipped_action_count": len(skipped),
                    "scheduled_run_count": len(scheduled_runs),
                    "scheduled_run_skip_reason": scheduled_run_skip_reason,
                    "scheduled_run_blocked_reasons": scheduled_run_blocking["blocked_reasons"],
                    "scheduled_run_blocked_steps": scheduled_run_blocking["blocked_steps"],
                    "actions": [str(item["action"]) for item in results],
                    "sources": sorted(
                        {
                            str(source)
                            for item in results
                            for source in _string_list(item.get("sources"))
                        }
                    ),
                },
            )
            self._session.commit()
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "applied" if applied_action_count else "noop",
            "requested_action_count": len(action_plan),
            "eligible_action_count": len(grouped),
            "applied_action_count": applied_action_count,
            "skipped_action_count": len(skipped),
            "summary": command_center["summary"],
            "results": results,
            "skipped": skipped,
            "scheduled_run_skip_reason": scheduled_run_skip_reason,
            "scheduled_run_blocked_reasons": scheduled_run_blocking["blocked_reasons"],
            "scheduled_run_blocked_steps": scheduled_run_blocking["blocked_steps"],
            "scheduled_run_count": len(scheduled_runs),
            "scheduled_runs": [_scheduled_run_payload(run) for run in scheduled_runs],
        }
