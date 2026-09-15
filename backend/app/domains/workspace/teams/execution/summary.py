from __future__ import annotations

from collections import Counter
from uuid import UUID

from backend.app.core.utils import dedupe_strings, string_list
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.runs.state import RunStatus
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.workspace.teams.execution.overview_contracts import (
    RISK_LEVELS,
    ExecutionBottleneck,
    MemberWorkload,
    SpecialistReassignment,
    StaffingGap,
    SummaryAction,
    severity_rank,
    uuid_list,
)
from backend.app.domains.workspace.teams.execution.overview_interventions import (
    operator_intervention_plan,
)


def summary_bottlenecks(
    *,
    task_items: list[dict[str, object]],
    member_items: list[MemberWorkload],
    staffing_gaps: list[StaffingGap],
    specialist_reassignments: list[SpecialistReassignment],
    steps: list[TaskStep],
    runs: list[AgentRun],
) -> list[ExecutionBottleneck]:
    bottlenecks: list[ExecutionBottleneck] = []
    if staffing_gaps:
        task_ids = sorted(
            {task_id for gap in staffing_gaps for task_id in uuid_list(gap.get("task_ids"))},
            key=str,
        )
        bottlenecks.append(
            _bottleneck(
                code="staffing_gap",
                severity="high",
                count=len(staffing_gaps),
                task_ids=task_ids,
                recommended_action="add_or_hire_team_member",
            )
        )

    if specialist_reassignments:
        task_ids = sorted(
            {
                task_id
                for reassignment in specialist_reassignments
                if isinstance((task_id := reassignment.get("task_id")), UUID)
            },
            key=str,
        )
        bottlenecks.append(
            _bottleneck(
                code="specialist_step_reassignment",
                severity="high"
                if any(
                    reassignment.get("step_status") == "failed"
                    for reassignment in specialist_reassignments
                )
                else "medium",
                count=len(specialist_reassignments),
                task_ids=task_ids,
                recommended_action="reassign_step",
            )
        )

    overloaded_members = [member for member in member_items if member.get("overloaded") is True]
    if overloaded_members:
        bottlenecks.append(
            _bottleneck(
                code="member_over_capacity",
                severity="high",
                count=len(overloaded_members),
                task_ids=[],
                recommended_action="rebalance_member_load",
            )
        )

    blocked_tasks = [task for task in task_items if task.get("blocked_reasons")]
    if blocked_tasks:
        bottlenecks.append(
            _bottleneck(
                code="blocked_tasks",
                severity="high"
                if any(task.get("risk_level") in {"critical", "high"} for task in blocked_tasks)
                else "medium",
                count=len(blocked_tasks),
                task_ids=_task_item_ids(blocked_tasks),
                recommended_action="inspect_task_diagnostics",
            )
        )

    high_risk_tasks = [
        task for task in task_items if task.get("risk_level") in {"critical", "high"}
    ]
    if high_risk_tasks:
        bottlenecks.append(
            _bottleneck(
                code="high_risk_tasks",
                severity="critical"
                if any(task.get("risk_level") == "critical" for task in high_risk_tasks)
                else "high",
                count=len(high_risk_tasks),
                task_ids=_task_item_ids(high_risk_tasks),
                recommended_action="review_high_risk_tasks",
            )
        )

    waiting_runtime_count = sum(1 for run in runs if run.status == RunStatus.WAITING_RUNTIME.value)
    if waiting_runtime_count:
        bottlenecks.append(
            _bottleneck(
                code="runtime_wait",
                severity="medium",
                count=waiting_runtime_count,
                task_ids=sorted(
                    {
                        run.task_id
                        for run in runs
                        if run.status == RunStatus.WAITING_RUNTIME.value and run.task_id
                    },
                    key=str,
                ),
                recommended_action="inspect_runtime_capacity",
            )
        )

    waiting_approval_runs = [run for run in runs if run.status == RunStatus.WAITING_APPROVAL.value]
    if waiting_approval_runs:
        bottlenecks.append(
            _bottleneck(
                code="approval_wait",
                severity="medium",
                count=len(waiting_approval_runs),
                task_ids=sorted(
                    {run.task_id for run in waiting_approval_runs if run.task_id},
                    key=str,
                ),
                recommended_action="review_pending_approvals",
            )
        )

    waiting_subworkflow_runs = [
        run for run in runs if run.status == RunStatus.WAITING_SUBWORKFLOW.value
    ]
    if waiting_subworkflow_runs:
        bottlenecks.append(
            _bottleneck(
                code="subworkflow_wait",
                severity="low",
                count=len(waiting_subworkflow_runs),
                task_ids=sorted(
                    {run.task_id for run in waiting_subworkflow_runs if run.task_id},
                    key=str,
                ),
                recommended_action="inspect_subworkflow_child_tasks",
            )
        )

    return sorted(
        bottlenecks,
        key=lambda item: (severity_rank(str(item["severity"])), str(item["code"])),
    )


def delivery_health(
    *,
    task_items: list[dict[str, object]],
    member_items: list[MemberWorkload],
    staffing_gaps: list[StaffingGap],
    bottlenecks: list[ExecutionBottleneck],
    total_capacity: int,
    active_member_tasks: int,
    available_member_capacity: int,
) -> dict[str, object]:
    reasons: list[str] = []
    score = 100
    high_risk_count = sum(
        1 for task in task_items if task.get("risk_level") in {"critical", "high"}
    )
    blocked_task_count = sum(1 for task in task_items if task.get("blocked_reasons"))
    overloaded_count = sum(1 for member in member_items if member.get("overloaded") is True)
    critical_bottleneck = any(item.get("severity") == "critical" for item in bottlenecks)

    if staffing_gaps:
        score -= 20
        reasons.append("staffing_gap")
    if high_risk_count:
        score -= min(35, high_risk_count * 25)
        reasons.append("high_risk_tasks")
    if blocked_task_count:
        score -= min(25, blocked_task_count * 15)
        reasons.append("blocked_tasks")
    if overloaded_count:
        score -= min(20, overloaded_count * 15)
        reasons.append("member_over_capacity")
    if total_capacity > 0 and active_member_tasks >= total_capacity:
        score -= 10
        reasons.append("team_capacity_full")

    score = max(score, 0)
    if critical_bottleneck or score <= 40:
        status = "critical"
    elif score <= 70:
        status = "degraded"
    elif score < 95:
        status = "attention"
    else:
        status = "healthy"

    return {
        "status": status,
        "score": score,
        "reasons": dedupe_strings(reasons),
        "bottleneck_count": len(bottlenecks),
        "high_risk_task_count": high_risk_count,
        "blocked_task_count": blocked_task_count,
        "overloaded_member_count": overloaded_count,
        "capacity_utilization": round(
            active_member_tasks / total_capacity,
            4,
        )
        if total_capacity > 0
        else 0.0,
        "available_member_capacity": available_member_capacity,
    }


def _bottleneck(
    *,
    code: str,
    severity: str,
    count: int,
    task_ids: list[UUID],
    recommended_action: str,
) -> ExecutionBottleneck:
    return {
        "code": code,
        "severity": severity,
        "count": count,
        "task_ids": task_ids,
        "recommended_action": recommended_action,
    }


def _task_item_ids(task_items: list[dict[str, object]]) -> list[UUID]:
    task_ids = [task.get("task_id") for task in task_items]
    return sorted([task_id for task_id in task_ids if isinstance(task_id, UUID)], key=str)

def overview_summary(
    *,
    tasks: list[Task],
    steps: list[TaskStep],
    runs: list[AgentRun],
    member_items: list[MemberWorkload],
    task_items: list[dict[str, object]],
    staffing_gaps: list[StaffingGap],
    specialist_reassignments: list[SpecialistReassignment],
) -> dict[str, object]:
    task_status_counts = Counter(task.status for task in tasks)
    step_status_counts = Counter(step.status for step in steps)
    run_status_counts = Counter(run.status for run in runs)
    risk_counts = Counter(str(item["risk_level"]) for item in task_items)
    total_capacity = sum(
        item["max_concurrent_tasks"]
        for item in member_items
        if item["status"] == "active" and item["accepts_tasks"] is True
    )
    active_member_tasks = sum(item["workspace_active_task_count"] for item in member_items)
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
        "staffing_gap_step_count": sum(item["step_count"] for item in staffing_gaps),
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
    staffing_gaps: list[StaffingGap],
    member_items: list[MemberWorkload],
    specialist_reassignments: list[SpecialistReassignment],
) -> list[SummaryAction]:
    grouped: dict[str, SummaryAction] = {}
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
        item_task_id = task.get("task_id")
        task_ids = [item_task_id] if isinstance(item_task_id, UUID) else []
        for action in string_list(task.get("recommended_actions")):
            _add_summary_action(grouped, action=action, task_ids=task_ids)

    return sorted(
        grouped.values(),
        key=lambda item: (-item["count"], item["action"]),
    )


def _add_summary_action(
    grouped: dict[str, SummaryAction],
    *,
    action: str,
    task_ids: list[UUID],
) -> None:
    item = grouped.setdefault(action, {"action": action, "count": 0, "task_ids": []})
    item["count"] += 1
    merged = set(item["task_ids"])
    merged.update(task_ids)
    item["task_ids"] = sorted(merged, key=str)
