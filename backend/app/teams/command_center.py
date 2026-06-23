from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.teams.command_center_action_plan import (
    _merged_action_plan,
    _provider_action_plan,
    _runtime_action_plan,
)
from backend.app.teams.command_center_apply import TeamCommandCenterActionApplier
from backend.app.teams.command_center_grouping import _group_applicable_actions
from backend.app.teams.command_center_payloads import (
    _provider_readiness_blocked,
    _runtime_payload,
    _scheduled_run_payload,
    _summary,
)
from backend.app.teams.command_center_utils import (
    _dict,
    _list,
    _string_list,
)
from backend.app.teams.execution_overview import TeamExecutionOverviewService
from backend.app.teams.provider_readiness import TeamProviderReadinessService
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.teams.scheduling_blocks import scheduled_run_blocking_summary
from backend.app.workers.queue.redis_queue import RedisQueue


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
    ) -> dict[str, object] | None:
        overview = TeamExecutionOverviewService(self._session).get_overview(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
        )
        if overview is None:
            return None

        handoff_queue = TaskExecutionDiagnosticsService(self._session).list_handoff_queue(
            workspace_id=workspace_id,
            limit=queue_limit,
            offset=0,
            team_id=team_id,
            include_terminal=False,
        )
        manager_queue = TaskManagerDiagnosticsService(self._session).list_manager_queue(
            workspace_id=workspace_id,
            limit=queue_limit,
            offset=0,
            team_id=team_id,
            include_healthy=False,
        )
        action_plan = _merged_action_plan(
            overview=overview,
            handoff_queue=handoff_queue,
            manager_queue=manager_queue,
        )
        runtime_state = TeamRuntimeService(self._session).get_state(
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
            "operating_policy": getattr(runtime_state, "operating_policy", {}),
            "memory_summary": getattr(runtime_state, "memory_summary", {}),
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
        queue: RedisQueue | None = None,
        runtime_control: RuntimeControlService | None = None,
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
            RunOrchestrationService(self._session, queue=queue).schedule_team_steps(
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
                    "scheduled_run_blocked_reasons": scheduled_run_blocking[
                        "blocked_reasons"
                    ],
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

