from __future__ import annotations

from collections import Counter
from uuid import UUID

from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.execution_overview_bottlenecks import (
    delivery_health,
    summary_bottlenecks,
)
from backend.app.teams.execution_overview_constants import RISK_LEVELS
from backend.app.teams.execution_overview_interventions import operator_intervention_plan
from backend.app.teams.execution_overview_utils import (
    string_list,
    uuid_list,
)


def overview_summary(
    *,
    tasks: list[Task],
    steps: list[TaskStep],
    runs: list[AgentRun],
    member_items: list[dict[str, object]],
    task_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    specialist_reassignments: list[dict[str, object]],
) -> dict[str, object]:
    task_status_counts = Counter(task.status for task in tasks)
    step_status_counts = Counter(step.status for step in steps)
    run_status_counts = Counter(run.status for run in runs)
    risk_counts = Counter(str(item["risk_level"]) for item in task_items)
    total_capacity = sum(
        int(item["max_concurrent_tasks"])
        for item in member_items
        if item["status"] == "active" and item["accepts_tasks"] is True
    )
    active_member_tasks = sum(int(item["workspace_active_task_count"]) for item in member_items)
    available_member_capacity = max(total_capacity - active_member_tasks, 0)
    bottlenecks = summary_bottlenecks(
        task_items=task_items,
        member_items=member_items,
        staffing_gaps=staffing_gaps,
        specialist_reassignments=specialist_reassignments,
        steps=steps,
        runs=runs,
    )
    recommended_actions = _summary_recommended_actions(
        task_items=task_items,
        staffing_gaps=staffing_gaps,
        member_items=member_items,
        specialist_reassignments=specialist_reassignments,
    )
    delivery_health_result = delivery_health(
        task_items=task_items,
        member_items=member_items,
        staffing_gaps=staffing_gaps,
        bottlenecks=bottlenecks,
        total_capacity=total_capacity,
        active_member_tasks=active_member_tasks,
        available_member_capacity=available_member_capacity,
    )
    return {
        "task_counts": dict(sorted(task_status_counts.items())),
        "step_counts": dict(sorted(step_status_counts.items())),
        "run_counts": dict(sorted(run_status_counts.items())),
        "active_run_phase_counts": _summary_phase_counts(task_items),
        "total_tasks": len(tasks),
        "needs_attention_tasks": sum(1 for item in task_items if item["needs_attention"]),
        "blocked_tasks": sum(1 for item in task_items if item["blocked_reasons"]),
        "risk_counts": {level: risk_counts.get(level, 0) for level in RISK_LEVELS},
        "high_risk_task_count": sum(risk_counts.get(level, 0) for level in ("critical", "high")),
        "member_count": len(member_items),
        "active_member_count": sum(1 for item in member_items if item["status"] == "active"),
        "accepting_member_count": sum(
            1
            for item in member_items
            if item["status"] == "active" and item["accepts_tasks"] is True
        ),
        "overloaded_member_count": sum(1 for item in member_items if item["overloaded"]),
        "staffing_gap_count": len(staffing_gaps),
        "staffing_gap_step_count": sum(int(item["step_count"]) for item in staffing_gaps),
        "specialist_reassignment_count": len(specialist_reassignments),
        "specialist_reassignments": specialist_reassignments,
        "total_member_capacity": total_capacity,
        "active_member_task_count": active_member_tasks,
        "available_member_capacity": available_member_capacity,
        "delivery_health": delivery_health_result,
        "bottlenecks": bottlenecks,
        "recommended_actions": recommended_actions,
        "intervention_plan": operator_intervention_plan(
            recommended_actions=recommended_actions,
            bottlenecks=bottlenecks,
            staffing_gaps=staffing_gaps,
            specialist_reassignments=specialist_reassignments,
            task_items=task_items,
        ),
    }


def _summary_phase_counts(task_items: list[dict[str, object]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for task in task_items:
        phase_counts = task.get("active_run_phase_counts")
        if not isinstance(phase_counts, dict):
            continue
        for phase, count in phase_counts.items():
            if isinstance(phase, str) and isinstance(count, int):
                counts[phase] += count
    return dict(sorted(counts.items()))


def _summary_recommended_actions(
    *,
    task_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    member_items: list[dict[str, object]],
    specialist_reassignments: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for gap in staffing_gaps:
        _add_summary_action(
            grouped,
            action=str(gap.get("recommended_action") or "add_or_hire_team_member"),
            task_ids=uuid_list(gap.get("task_ids")),
        )
    for member in member_items:
        if member.get("overloaded") is True:
            _add_summary_action(grouped, action="rebalance_member_load", task_ids=[])
        if "member_not_accepting_tasks" in string_list(member.get("blocked_reasons")):
            _add_summary_action(grouped, action="review_member_availability", task_ids=[])
    for reassignment in specialist_reassignments:
        task_id = reassignment.get("task_id")
        task_ids = [task_id] if isinstance(task_id, UUID) else []
        _add_summary_action(grouped, action="reassign_step", task_ids=task_ids)
    for task in task_items:
        task_id = task.get("task_id")
        task_ids = [task_id] if isinstance(task_id, UUID) else []
        for action in string_list(task.get("recommended_actions")):
            _add_summary_action(grouped, action=action, task_ids=task_ids)

    return sorted(
        grouped.values(),
        key=lambda item: (-int(item["count"]), str(item["action"])),
    )


def _add_summary_action(
    grouped: dict[str, dict[str, object]],
    *,
    action: str,
    task_ids: list[UUID],
) -> None:
    item = grouped.setdefault(action, {"action": action, "count": 0, "task_ids": []})
    item["count"] = int(item["count"]) + 1
    existing = item["task_ids"] if isinstance(item["task_ids"], list) else []
    merged = {task_id for task_id in existing if isinstance(task_id, UUID)}
    merged.update(task_ids)
    item["task_ids"] = sorted(merged, key=str)
