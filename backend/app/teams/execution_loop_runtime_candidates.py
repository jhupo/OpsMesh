from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Literal, TypedDict
from uuid import UUID

from backend.app.core.typing import datetime_or_none, positive_int_or_default
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.teams.execution_loop_constants import TEAM_RUNTIME_DEFAULT_LOOP_INTERVAL_SECONDS
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_STATUS_KEY,
)


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
