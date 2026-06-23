from __future__ import annotations

from backend.app.security.redaction import redact_sensitive_payload
from backend.app.teams.operations_console_runtime_payloads import (
    _runtime_blocked_step_suggested_actions,
    _runtime_blocked_steps_payload,
    _runtime_queue_suggested_actions,
)
from backend.app.teams.operations_console_utils import _dict, _int_value, _positive_int
from backend.app.teams.runtime import TeamRuntimeState


def _controls_payload(
    runtime_state: TeamRuntimeState,
    command_center: dict[str, object] | None,
    runtime_queue: dict[str, object],
    provider_management: dict[str, object],
    runtime_blocking: dict[str, object],
) -> dict[str, object]:
    action_plan = (
        list(command_center.get("action_plan") or []) if command_center is not None else []
    )
    provider_management_actions = [
        item
        for item in provider_management.get("suggested_actions", [])
        if isinstance(item, dict)
    ]
    return {
        "can_start": runtime_state.status != "running",
        "can_pause": runtime_state.status == "running",
        "can_resume": runtime_state.status == "paused",
        "can_stop": runtime_state.status != "stopped",
        "can_ensure_workspace_runtime": (
            runtime_state.workspace_runtime_id is None
            or runtime_state.runtime_status != "running"
            or runtime_state.runtime_health in {"stale", "degraded"}
        ),
        "suggested_actions": [
            item
            for item in [*provider_management_actions, *action_plan]
            if isinstance(item, dict)
            and item.get("source") in {
                "provider_management",
                "provider_readiness",
                "team_runtime",
            }
        ]
        + _runtime_queue_suggested_actions(runtime_queue)
        + _runtime_blocked_step_suggested_actions(runtime_blocking),
    }


def _readiness_payload(
    runtime_state: TeamRuntimeState,
    command_center: dict[str, object] | None,
    runtime_queue: dict[str, object],
    provider_management: dict[str, object],
    runtime_blocking: dict[str, object],
    mailbox: dict[str, object],
    controls: dict[str, object],
) -> dict[str, object]:
    runtime_metadata = dict(runtime_state.metadata)
    stall_count = _int_value(runtime_metadata.get("stall_count"))
    stall_threshold = _positive_int(runtime_metadata.get("stall_threshold"), 3)
    stalled_at = runtime_metadata.get("stalled_at")
    provider_blocked = any(
        isinstance(item, dict) and item.get("readiness_status") == "blocked"
        for item in provider_management.get("agent_bindings", [])
        if isinstance(item, dict)
    )
    blocked_step_count = _int_value(
        _runtime_blocked_steps_payload(runtime_blocking).get("count")
    )
    queue_backlog = (
        _int_value(runtime_queue.get("queued"))
        + _int_value(runtime_queue.get("scheduled_retry"))
        + _int_value(runtime_queue.get("dead_letter"))
    )
    suggested_actions = [
        item for item in controls.get("suggested_actions", []) if isinstance(item, dict)
    ]
    suggested_actions.sort(
        key=lambda item: _int_value(item.get("priority")),
        reverse=True,
    )
    next_action = suggested_actions[0] if suggested_actions else None
    readiness_status = _readiness_status(
        runtime_state=runtime_state,
        stalled=bool(stalled_at) or stall_count >= stall_threshold,
        provider_blocked=provider_blocked,
        blocked_step_count=blocked_step_count,
        queue_backlog=queue_backlog,
    )
    return redact_sensitive_payload(
        {
            "status": readiness_status,
            "ready": readiness_status == "ready",
            "runtime_health": runtime_state.runtime_health,
            "runtime_status": runtime_state.status,
            "workspace_runtime_status": runtime_state.runtime_status,
            "stall": {
                "count": stall_count,
                "threshold": stall_threshold,
                "reason": runtime_metadata.get("stall_reason"),
                "stalled_at": stalled_at,
            },
            "mailbox_unread_count": _int_value(mailbox.get("unread_count")),
            "queue_backlog": queue_backlog,
            "blocked_step_count": blocked_step_count,
            "provider_blocked": provider_blocked,
            "action_plan_count": _int_value(
                _dict(command_center.get("summary") if command_center else {}).get(
                    "action_plan_count"
                )
            ),
            "next_operator_action": next_action,
        }
    )


def _readiness_status(
    *,
    runtime_state: TeamRuntimeState,
    stalled: bool,
    provider_blocked: bool,
    blocked_step_count: int,
    queue_backlog: int,
) -> str:
    if runtime_state.status in {"paused", "stopped"}:
        return runtime_state.status
    if stalled:
        return "stalled"
    if provider_blocked:
        return "provider_blocked"
    if runtime_state.runtime_health in {"stale", "degraded"}:
        return runtime_state.runtime_health
    if blocked_step_count > 0:
        return "blocked"
    if queue_backlog > 0:
        return "working"
    if runtime_state.runtime_health == "healthy" and runtime_state.runtime_status == "running":
        return "ready"
    return runtime_state.runtime_health or "unknown"
