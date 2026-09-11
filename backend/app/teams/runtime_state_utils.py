from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.agent_messages.models import AgentMessage
from backend.app.core.typing import datetime_or_none, int_or_zero
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.teams.runtime_constants import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_PAUSED,
    TEAM_RUNTIME_STALL_STATUSES,
    TEAM_RUNTIME_STALL_THRESHOLD,
    TEAM_RUNTIME_STOPPED,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.teams.runtime_refs import _dict_or_none


def _last_iteration(
    runtime_metadata: dict[str, object],
    message: AgentMessage | None,
) -> dict[str, object] | None:
    metadata_iteration = _dict_or_none(runtime_metadata.get("last_iteration"))
    if metadata_iteration is not None:
        return metadata_iteration
    if message is None:
        return None
    payload = message.payload if isinstance(message.payload, dict) else {}
    message_iteration = _dict_or_none(payload.get("iteration"))
    if message_iteration is not None:
        return message_iteration
    return {
        "status": str(payload.get("status") or "unknown"),
        "summary": _dict_or_none(payload.get("summary")) or {},
        "recorded_at": message.created_at.isoformat(),
    }


def _runtime_health(
    *,
    status: str,
    runtime: WorkspaceRuntime | None,
    metadata: dict[str, object],
    generated_at: datetime,
) -> str:
    if status == TEAM_RUNTIME_STOPPED:
        return "stopped"
    if status == TEAM_RUNTIME_PAUSED:
        return "paused"
    if runtime is not None and (
        runtime.status != "running" or runtime.connection_status in {"offline", "error"}
    ):
        return "degraded"
    if metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY) is not None and runtime is None:
        return "degraded"

    last_heartbeat_at = datetime_or_none(metadata.get("last_heartbeat_at"))
    if last_heartbeat_at is None:
        return "starting"
    if generated_at - last_heartbeat_at > timedelta(
        seconds=TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS
    ):
        return "stale"
    if metadata.get("heartbeat_status") == "skipped":
        return "degraded"
    if _runtime_stalled(metadata):
        return "degraded"
    if _last_worker_failure_active(metadata):
        return "degraded"
    return "healthy"


def _stall_metadata_update(
    *,
    runtime_metadata: dict[str, object],
    status: str,
    summary: dict[str, object],
    recorded_at: str,
) -> dict[str, object]:
    if status not in TEAM_RUNTIME_STALL_STATUSES:
        return {}
    stall_count = _int(runtime_metadata.get("stall_count")) + 1
    reason = _stall_reason(summary)
    update: dict[str, object] = {
        "stall_count": stall_count,
        "stall_reason": reason,
        "stall_threshold": TEAM_RUNTIME_STALL_THRESHOLD,
    }
    if stall_count >= TEAM_RUNTIME_STALL_THRESHOLD:
        update["stalled_at"] = runtime_metadata.get("stalled_at") or recorded_at
    return update


def _stall_reason(summary: dict[str, object]) -> str:
    for key in ("reason", "scheduled_run_skip_reason"):
        value = summary.get(key)
        if isinstance(value, str) and value:
            return value
    if _int(summary.get("eligible_action_count")) > 0:
        return "eligible_actions_not_applied"
    if _int(summary.get("skipped_task_count")) > 0:
        return "tasks_not_finalizable"
    return "no_progress"


def _runtime_stalled(metadata: dict[str, object]) -> bool:
    if metadata.get("stalled_at"):
        return True
    return _int(metadata.get("stall_count")) >= TEAM_RUNTIME_STALL_THRESHOLD


def _last_worker_failure_active(metadata: dict[str, object]) -> bool:
    failure = metadata.get("last_worker_failure")
    if not isinstance(failure, dict):
        return False
    status = failure.get("status")
    return status in {"retrying", "failed"}


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


def _int(value: object) -> int:
    return int_or_zero(value)
