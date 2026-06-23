from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from backend.app.operations.utils import ensure_aware_utc
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)


def team_runtime_metadata(team: AgentTeam) -> dict[str, object]:
    policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
    runtime_metadata = policy.get(TEAM_RUNTIME_STATUS_KEY)
    return dict(runtime_metadata) if isinstance(runtime_metadata, dict) else {}


def team_runtime_workspace_runtime_id(team: AgentTeam) -> UUID | None:
    runtime_id = team_runtime_metadata(team).get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY)
    if isinstance(runtime_id, UUID):
        return runtime_id
    if isinstance(runtime_id, str) and runtime_id:
        try:
            return UUID(runtime_id)
        except ValueError:
            return None
    return None


def team_runtime_health_for_metrics(
    *,
    runtime_metadata: dict[str, object],
    runtime: WorkspaceRuntime | None,
    generated_at: datetime,
) -> str:
    status = runtime_metadata.get("status")
    if status == "paused":
        return "paused"
    if status == "stopped" or status is None:
        return "stopped"
    if runtime is None and runtime_metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY):
        return "degraded"
    if runtime is not None and (
        runtime.status != "running" or runtime.connection_status in {"offline", "error"}
    ):
        return "degraded"
    if runtime_metadata.get("heartbeat_status") == "skipped":
        return "degraded"
    last_heartbeat_at = datetime_from_metadata(runtime_metadata.get("last_heartbeat_at"))
    if last_heartbeat_at is None:
        return "starting"
    if generated_at - last_heartbeat_at > timedelta(
        seconds=TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS
    ):
        return "stale"
    return "healthy"


def datetime_from_metadata(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return ensure_aware_utc(parsed)
