from collections.abc import Iterable
from uuid import UUID

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_sensitive_text
from opsmesh.shared.utils import (
    dict_or_empty,
    int_or_zero,
    positive_int_or_default,
    string_list,
    uuid_or_none,
)
from opsmesh.teams.organization.policy_payloads import visible_task_policy
from opsmesh.teams.sessions.service import TEAM_RUNTIME_RUNNING
from opsmesh.teams.sessions.state_builder import TeamOperationSnapshot


def _dict(value: object) -> dict[str, object]:
    return dict_or_empty(value)


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _int(value: object) -> int:
    return int_or_zero(value)


def _string_list(value: object) -> list[str]:
    return string_list(value)


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]


def _uuid_value(value: object) -> UUID | None:
    return value if isinstance(value, UUID) else None


def _summary(
    *,
    overview: dict[str, object],
    handoff_queue: dict[str, object],
    manager_queue: dict[str, object],
    runtime_state: TeamOperationSnapshot,
    provider_readiness: dict[str, object],
    action_plan: list[dict[str, object]],
    queue_limit: int,
) -> dict[str, object]:
    overview_summary = _dict(overview.get("summary"))
    source_counts: dict[str, int] = {}
    for item in action_plan:
        source = item.get("source")
        if isinstance(source, str):
            source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "team_status": _dict(overview.get("team")).get("status"),
        "delivery_health": overview_summary.get("delivery_health"),
        "total_tasks": _int(overview_summary.get("total_tasks")),
        "needs_attention_tasks": _int(overview_summary.get("needs_attention_tasks")),
        "blocked_tasks": _int(overview_summary.get("blocked_tasks")),
        "handoff_queue_total": _int(handoff_queue.get("total")),
        "manager_queue_total": _int(manager_queue.get("total")),
        "runtime_status": runtime_state.status,
        "workspace_runtime_id": runtime_state.workspace_runtime_id,
        "workspace_runtime_status": runtime_state.runtime_status,
        "runtime_space_id": runtime_state.runtime_space_id,
        "runtime_ready": _runtime_ready(runtime_state),
        "provider_readiness": _provider_readiness_summary(provider_readiness),
        "memory_entry_count": _int(_dict(runtime_state.memory_summary).get("active_entry_count")),
        "team_memory_entry_count": _int(
            _dict(runtime_state.memory_summary).get("team_entry_count")
        ),
        "task_scan_limit": overview_summary.get("task_scan_limit"),
        "scanned_task_count": overview_summary.get("scanned_task_count"),
        "tasks_truncated": overview_summary.get("tasks_truncated", False),
        "queue_limit": queue_limit,
        "queue_truncated": overview_summary.get("tasks_truncated") is True
        or _int(handoff_queue.get("total")) > queue_limit
        or _int(manager_queue.get("total")) > queue_limit,
        "action_plan_count": len(action_plan),
        "action_plan_source_counts": dict(sorted(source_counts.items())),
    }


def _runtime_payload(
    runtime_state: TeamOperationSnapshot, *, provider_readiness: dict[str, object] | None = None
) -> dict[str, object]:
    return {
        "status": runtime_state.status,
        "workspace_runtime_id": runtime_state.workspace_runtime_id,
        "runtime_status": runtime_state.runtime_status,
        "runtime_space_id": runtime_state.runtime_space_id,
        "thread_id": runtime_state.thread_id,
        "team_session_id": runtime_state.team_session_id,
        "member_session_count": runtime_state.member_session_count,
        "ready": _runtime_ready(runtime_state),
        "provider_readiness": _provider_readiness_summary(provider_readiness or {}),
        "operating_policy": runtime_state.operating_policy,
        "memory_summary": runtime_state.memory_summary,
    }


def _provider_readiness_summary(provider_readiness: dict[str, object]) -> dict[str, object]:
    return {
        "status": provider_readiness.get("status", "unknown"),
        "member_count": _int(provider_readiness.get("member_count")),
        "runtime_participant_count": _int(provider_readiness.get("runtime_participant_count")),
        "ready_member_count": _int(provider_readiness.get("ready_member_count")),
        "degraded_member_count": _int(provider_readiness.get("degraded_member_count")),
        "blocked_member_count": _int(provider_readiness.get("blocked_member_count")),
        "runtime_ready_member_count": _int(provider_readiness.get("runtime_ready_member_count")),
        "runtime_degraded_member_count": _int(
            provider_readiness.get("runtime_degraded_member_count")
        ),
        "runtime_blocked_member_count": _int(
            provider_readiness.get("runtime_blocked_member_count")
        ),
        "requires_operator_attention": provider_readiness.get("requires_operator_attention", False)
        is True,
        "blocking_reasons": _dict(provider_readiness.get("blocking_reasons")),
        "warning_reasons": _dict(provider_readiness.get("warning_reasons")),
        "runtime_blocking_reasons": _dict(provider_readiness.get("runtime_blocking_reasons")),
        "runtime_warning_reasons": _dict(provider_readiness.get("runtime_warning_reasons")),
    }


def _runtime_ready(runtime_state: TeamOperationSnapshot) -> bool:
    return (
        runtime_state.status == TEAM_RUNTIME_RUNNING
        and runtime_state.workspace_runtime_id is not None
        and (runtime_state.runtime_status == "active")
    )


def _provider_readiness_blocked(provider_readiness: dict[str, object]) -> bool:
    return _int(provider_readiness.get("runtime_blocked_member_count")) > 0


def _scheduled_run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "agent_run_id": run.id,
        "task_id": run.task_id,
        "task_step_id": run.task_step_id,
        "agent_profile_id": run.agent_profile_id,
        "runtime_space_id": run.runtime_space_id,
        "status": run.status,
    }


def _unique_strings(values: Iterable[object]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if not isinstance(value, str) or value in unique:
            continue
        unique.append(value)
    return unique


def _uuid_or_none(value: object) -> UUID | None:
    return uuid_or_none(value)


def _redacted_dict_or_none(value: dict[str, object] | None) -> dict[str, object] | None:
    return redact_sensitive_payload(dict(value)) if isinstance(value, dict) else None


def _positive_int(value: object, default: int) -> int:
    return positive_int_or_default(value, default)


def _int_value(value: object) -> int:
    return int_or_zero(value)


def _visible_task_policy(value: object) -> dict[str, object]:
    return visible_task_policy(value)


def _preview(value: str, *, max_chars: int = 240) -> str:
    text = " ".join(value.split())
    text = redact_sensitive_text(text)
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."
