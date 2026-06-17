from __future__ import annotations

from uuid import UUID

from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import TaskStep
from backend.app.teams.execution_overview_utils import (
    dedupe_strings,
    severity_rank,
    uuid_list,
)


def summary_bottlenecks(
    *,
    task_items: list[dict[str, object]],
    member_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    specialist_reassignments: list[dict[str, object]],
    steps: list[TaskStep],
    runs: list[AgentRun],
) -> list[dict[str, object]]:
    bottlenecks: list[dict[str, object]] = []
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

    return sorted(
        bottlenecks,
        key=lambda item: (severity_rank(str(item["severity"])), str(item["code"])),
    )


def delivery_health(
    *,
    task_items: list[dict[str, object]],
    member_items: list[dict[str, object]],
    staffing_gaps: list[dict[str, object]],
    bottlenecks: list[dict[str, object]],
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
) -> dict[str, object]:
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
