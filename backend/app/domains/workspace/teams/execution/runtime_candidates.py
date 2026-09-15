from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Literal, TypedDict
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.common.values import datetime_or_none, positive_int_or_default, uuid_or_none
from backend.app.domains.workspace.teams.execution.loop_support import (
    TEAM_RUNTIME_DEFAULT_LOOP_INTERVAL_SECONDS,
)
from backend.app.domains.workspace.teams.execution.queue_repository import (
    TeamExecutionLoopQueueRepository,
)
from backend.app.domains.workspace.teams.models import AgentTeam
from backend.app.domains.workspace.teams.providers.service import (
    TeamProviderReadinessService,
)
from backend.app.domains.workspace.teams.runtime.service import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.runtime.environment.models import WorkspaceRuntime


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
    ) -> tuple[ReadyRuntimeCandidate | BlockedRuntimeCandidate | None, str | None]:
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

        workspace_runtime_id = uuid_or_none(
            runtime_metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY)
        )
        workspace_runtime = (
            self._repo.workspace_runtime(team.workspace_id, workspace_runtime_id)
            if workspace_runtime_id is not None
            else None
        )
        last_heartbeat_at = datetime_or_none(runtime_metadata.get("last_heartbeat_at"))
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
            "skip": False,
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
        scheduled_priority = _scheduled_runtime_priority(
            runtime_metadata=runtime_metadata,
            generated_at=generated_at,
        )
        if scheduled_priority is None:
            return 0, "", "scheduled_team_runtime_not_due"
        return scheduled_priority, "scheduled_team_runtime", None
    if runtime_health == "degraded":
        return 20, "degraded_team_runtime", None
    if runtime_health == "stale":
        return 15, "stale_team_runtime", None
    return 0, "running_team_runtime", None

class TeamLoopCandidate(TypedDict):
    workspace_id: UUID
    team_id: UUID
    requested_by_user_id: UUID
    priority: int
    trigger: str
    task_id: UUID | None
    routing: dict[str, object]


class ReadyRuntimeCandidate(TypedDict):
    skip: Literal[False]
    priority: int
    trigger: str
    runtime_health: str
    workspace_runtime_id: str | None
    last_heartbeat_at: str | None


class BlockedRuntimeCandidate(TypedDict):
    skip: Literal[True]
    trigger: str
    runtime_health: str
    provider_readiness: dict[str, object]


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


def _scheduled_runtime_priority(
    *,
    runtime_metadata: dict[str, object],
    generated_at: datetime,
) -> int | None:
    scheduling_policy = _runtime_scheduling_policy(runtime_metadata)
    if scheduling_policy.get("scheduled_loop_enabled") is False:
        return None
    loop_interval_seconds = positive_int_or_default(
        scheduling_policy.get("loop_interval_seconds"),
        TEAM_RUNTIME_DEFAULT_LOOP_INTERVAL_SECONDS,
    )
    last_iteration_at = _last_iteration_recorded_at(runtime_metadata) or datetime_or_none(
        runtime_metadata.get("last_heartbeat_at")
    )
    if last_iteration_at is not None and generated_at - last_iteration_at < timedelta(
        seconds=loop_interval_seconds
    ):
        return None
    return positive_int_or_default(scheduling_policy.get("priority"), 5)


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
    return datetime_or_none(last_iteration.get("recorded_at"))


def _record_runtime_scheduler_scan(
    team: AgentTeam,
    *,
    status: str,
    reason: str | None,
    scanned_at: datetime,
    window: int,
    runtime_candidate: Mapping[str, object] | None = None,
) -> None:
    policy = dict(team.default_task_policy or {})
    stored_runtime = policy.get(TEAM_RUNTIME_STATUS_KEY)
    runtime_metadata = dict(stored_runtime) if isinstance(stored_runtime, dict) else {}
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


def _scheduler_scan_candidate(candidate: TeamLoopCandidate) -> dict[str, object]:
    return {
        "trigger": candidate["trigger"],
        **candidate["routing"],
    }


def _provider_readiness_blocks_runtime(provider_readiness: dict[str, object]) -> bool:
    value = provider_readiness.get("runtime_blocked_member_count")
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _provider_blocked_runtime_candidate(
    provider_readiness: dict[str, object],
) -> BlockedRuntimeCandidate:
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


def _increment_skip_reason(skipped_reasons: dict[str, int], reason: str) -> None:
    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1


def _team_loop_candidate(
    *,
    workspace_id: UUID,
    team_id: UUID,
    requested_by_user_id: UUID,
    priority: int,
    trigger: str,
    task_id: UUID | None,
    routing: dict[str, object] | None = None,
) -> TeamLoopCandidate:
    return {
        "workspace_id": workspace_id,
        "team_id": team_id,
        "requested_by_user_id": requested_by_user_id,
        "priority": priority,
        "trigger": trigger,
        "task_id": task_id,
        "routing": routing or {},
    }
