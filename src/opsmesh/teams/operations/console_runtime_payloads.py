from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from opsmesh.shared.security.redaction import redact_sensitive_payload
from opsmesh.shared.utils import (
    dedupe_strings,
    dict_list,
    dict_or_empty,
    int_or_zero,
)
from opsmesh.teams.sessions.service import TeamOperationSnapshot


def runtime_payload(
    state: TeamOperationSnapshot,
    *,
    runtime_queue: dict[str, object],
    runtime_blocking: dict[str, object],
) -> dict[str, object]:
    return {
        "status": state.status,
        "workspace_runtime_id": state.workspace_runtime_id,
        "runtime_status": state.runtime_status,
        "runtime_space_id": state.runtime_space_id,
        "runtime_health": state.runtime_health,
        "thread_id": state.thread_id,
        "team_session_id": state.team_session_id,
        "team_session_key": state.team_session_key,
        "member_session_count": state.member_session_count,
        "member_agent_ids": state.member_agent_ids,
        "last_advancement": _redacted_dict_or_none(state.last_advancement),
        "last_message_at": state.last_message_at,
        "operating_policy": redact_sensitive_payload(dict(state.operating_policy)),
        "memory_summary": redact_sensitive_payload(dict(state.memory_summary)),
        "scheduling": redact_sensitive_payload(
            _runtime_scheduling_payload(state.metadata, state.generated_at)
        ),
        "blocked_steps": redact_sensitive_payload(runtime_blocked_steps_payload(runtime_blocking)),
        "queue": redact_sensitive_payload(runtime_queue),
        "ready": state.status == "running"
        and state.workspace_runtime_id is not None
        and state.runtime_status == "active"
        and state.runtime_health == "healthy",
        "metadata": redact_sensitive_payload(dict(state.metadata)),
    }


def runtime_queue_suggested_actions(
    runtime_queue: dict[str, object],
) -> list[dict[str, object]]:
    unresolved = int_or_zero(runtime_queue.get("unknown"))
    if unresolved == 0:
        return []
    return [
        {
            "source": "team_orchestration",
            "automation": "team_runtime_control",
            "action": "reconcile_team_request",
            "priority": 95,
            "reason": "team_request_outcome_unknown",
            "count": unresolved,
        }
    ]


def runtime_blocked_steps_payload(runtime_blocking: dict[str, object]) -> dict[str, object]:
    steps = dict_list(runtime_blocking.get("blocked_steps"))
    return {
        "count": len(steps),
        "blocked_reasons": dict_or_empty(runtime_blocking.get("blocked_reasons")),
        "latest": steps[:10],
        "truncated": len(steps) > 10,
    }


def runtime_blocked_step_suggested_actions(
    runtime_blocking: dict[str, object],
) -> list[dict[str, object]]:
    payload = runtime_blocked_steps_payload(runtime_blocking)
    count = int_or_zero(payload.get("count"))
    if count <= 0:
        return []
    steps = dict_list(payload.get("latest"))
    return [
        {
            "source": "team_runtime",
            "automation": "team_runtime_control",
            "action": "review_scheduling_blocks",
            "priority": 90,
            "reason": "team_runtime_step_scheduling_blocked",
            "count": count,
            "blocked_reasons": payload["blocked_reasons"],
            "task_ids": _unique_strings(item.get("task_id") for item in steps),
            "task_step_ids": _unique_strings(item.get("task_step_id") for item in steps),
        }
    ]


def _runtime_scheduling_payload(
    metadata: dict[str, object],
    generated_at: datetime,
) -> dict[str, object]:
    return {
        "mode": "event_driven",
        "last_advancement": dict_or_empty(metadata.get("last_advancement")),
    }


def _unique_strings(values: Iterable[object]) -> list[str]:
    return dedupe_strings(str(value) for value in values if value is not None)


def _redacted_dict_or_none(value: dict[str, object] | None) -> dict[str, object] | None:
    return redact_sensitive_payload(dict(value)) if isinstance(value, dict) else None
