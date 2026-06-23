from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.app.teams.execution_loop_queue_repository import (
    TeamExecutionLoopQueueRepository,
)
from backend.app.teams.execution_loop_runtime_candidates import (
    _datetime_or_none,
    _provider_blocked_runtime_candidate,
    _provider_readiness_blocks_runtime,
    _runtime_candidate_health,
    _scheduled_runtime_candidate,
    _uuid_or_none,
)
from backend.app.teams.models import AgentTeam
from backend.app.teams.provider_readiness import TeamProviderReadinessService
from backend.app.teams.runtime import (
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)


class TeamExecutionLoopRuntimeCandidateBuilder:
    """Determine whether a running team runtime should be scheduled."""

    def __init__(
        self,
        *,
        session: Session,
        repo: TeamExecutionLoopQueueRepository,
    ) -> None:
        self._session = session
        self._repo = repo

    def runtime_candidate(
        self,
        team: AgentTeam,
        *,
        generated_at: datetime,
    ) -> tuple[dict[str, object] | None, str | None]:
        runtime_metadata = _runtime_metadata(team)
        if runtime_metadata is None:
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
            self._repo.workspace_runtime(team.workspace_id, workspace_runtime_id)
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
        priority, trigger, skip_reason = _runtime_priority_and_trigger(
            runtime_health=runtime_health,
            runtime_metadata=runtime_metadata,
            generated_at=generated_at,
        )
        if skip_reason is not None:
            return None, skip_reason
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


def _runtime_metadata(team: AgentTeam) -> dict[str, object] | None:
    policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
    runtime_metadata = policy.get(TEAM_RUNTIME_STATUS_KEY)
    return runtime_metadata if isinstance(runtime_metadata, dict) else None


def _runtime_priority_and_trigger(
    *,
    runtime_health: str,
    runtime_metadata: dict[str, object],
    generated_at: datetime,
) -> tuple[int, str, str | None]:
    if runtime_health == "healthy":
        scheduled_candidate = _scheduled_runtime_candidate(
            runtime_metadata=runtime_metadata,
            generated_at=generated_at,
        )
        if scheduled_candidate is None:
            return 0, "", "scheduled_team_runtime_not_due"
        return int(scheduled_candidate["priority"]), "scheduled_team_runtime", None
    if runtime_health == "degraded":
        return 20, "degraded_team_runtime", None
    if runtime_health == "stale":
        return 15, "stale_team_runtime", None
    return 0, "running_team_runtime", None
