from __future__ import annotations

from backend.app.runs.models import AgentRun
from backend.app.teams.command_center_utils import _dict, _int
from backend.app.teams.runtime import TEAM_RUNTIME_RUNNING


def _summary(
    *,
    overview: dict[str, object],
    handoff_queue: dict[str, object],
    manager_queue: dict[str, object],
    runtime_state: object,
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
        "runtime_status": getattr(runtime_state, "status", None),
        "workspace_runtime_id": getattr(runtime_state, "workspace_runtime_id", None),
        "workspace_runtime_status": getattr(runtime_state, "runtime_status", None),
        "runtime_space_id": getattr(runtime_state, "runtime_space_id", None),
        "runtime_ready": _runtime_ready(runtime_state),
        "provider_readiness": _provider_readiness_summary(provider_readiness),
        "memory_entry_count": _int(
            _dict(getattr(runtime_state, "memory_summary", {})).get("active_entry_count")
        ),
        "team_memory_entry_count": _int(
            _dict(getattr(runtime_state, "memory_summary", {})).get("team_entry_count")
        ),
        "queue_limit": queue_limit,
        "queue_truncated": (
            _int(handoff_queue.get("total")) > queue_limit
            or _int(manager_queue.get("total")) > queue_limit
        ),
        "action_plan_count": len(action_plan),
        "action_plan_source_counts": dict(sorted(source_counts.items())),
    }


def _runtime_payload(
    runtime_state: object,
    *,
    provider_readiness: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "status": getattr(runtime_state, "status", None),
        "workspace_runtime_id": getattr(runtime_state, "workspace_runtime_id", None),
        "runtime_status": getattr(runtime_state, "runtime_status", None),
        "runtime_space_id": getattr(runtime_state, "runtime_space_id", None),
        "thread_id": getattr(runtime_state, "thread_id", None),
        "team_session_id": getattr(runtime_state, "team_session_id", None),
        "member_session_count": getattr(runtime_state, "member_session_count", 0),
        "ready": _runtime_ready(runtime_state),
        "provider_readiness": _provider_readiness_summary(provider_readiness or {}),
        "operating_policy": getattr(runtime_state, "operating_policy", {}),
        "memory_summary": getattr(runtime_state, "memory_summary", {}),
    }


def _provider_readiness_summary(provider_readiness: dict[str, object]) -> dict[str, object]:
    return {
        "status": provider_readiness.get("status", "unknown"),
        "member_count": _int(provider_readiness.get("member_count")),
        "runtime_participant_count": _int(provider_readiness.get("runtime_participant_count")),
        "ready_member_count": _int(provider_readiness.get("ready_member_count")),
        "degraded_member_count": _int(provider_readiness.get("degraded_member_count")),
        "blocked_member_count": _int(provider_readiness.get("blocked_member_count")),
        "runtime_ready_member_count": _int(
            provider_readiness.get("runtime_ready_member_count")
        ),
        "runtime_degraded_member_count": _int(
            provider_readiness.get("runtime_degraded_member_count")
        ),
        "runtime_blocked_member_count": _int(
            provider_readiness.get("runtime_blocked_member_count")
        ),
        "requires_operator_attention": provider_readiness.get(
            "requires_operator_attention",
            False,
        )
        is True,
        "blocking_reasons": _dict(provider_readiness.get("blocking_reasons")),
        "warning_reasons": _dict(provider_readiness.get("warning_reasons")),
        "runtime_blocking_reasons": _dict(
            provider_readiness.get("runtime_blocking_reasons")
        ),
        "runtime_warning_reasons": _dict(provider_readiness.get("runtime_warning_reasons")),
    }


def _runtime_ready(runtime_state: object) -> bool:
    return (
        getattr(runtime_state, "status", None) == TEAM_RUNTIME_RUNNING
        and getattr(runtime_state, "workspace_runtime_id", None) is not None
        and getattr(runtime_state, "runtime_status", None) == "running"
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
