from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from backend.app.artifacts.models import Artifact
from backend.app.core.typing import int_or_zero
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}


def task_summary(tasks: list[Task]) -> dict[str, object]:
    status_counts = counts(task.status for task in tasks)
    active_tasks = sum(1 for task in tasks if task.status in {"queued", "running", "blocked"})
    return {
        "task_count": len(tasks),
        "active_task_count": active_tasks,
        "blocked_task_count": status_counts.get("blocked", 0),
        "failed_task_count": status_counts.get("failed", 0),
        "completed_task_count": status_counts.get("completed", 0),
        "task_status_counts": status_counts,
    }


def execution_summary(runs: list[AgentRun]) -> dict[str, object]:
    status_counts = counts(run.status for run in runs)
    return {
        "run_count": len(runs),
        "active_run_count": sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES),
        "waiting_runtime_run_count": status_counts.get(RunStatus.WAITING_RUNTIME.value, 0),
        "failed_run_count": status_counts.get(RunStatus.FAILED.value, 0),
        "run_status_counts": status_counts,
    }


def delivery_summary(
    tasks: list[Task],
    steps: list[TaskStep],
    artifacts: list[Artifact],
) -> dict[str, object]:
    task_by_id = {task.id: task for task in tasks}
    artifacts_by_step: dict[UUID, list[Artifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.task_step_id is not None:
            artifacts_by_step[artifact.task_step_id].append(artifact)
    expected_total = 0
    missing_total = 0
    for step in steps:
        if step.task_id not in task_by_id:
            continue
        expected = [item for item in step.expected_artifacts if isinstance(item, str)]
        produced = {artifact.artifact_type for artifact in artifacts_by_step.get(step.id, [])}
        expected_total += len(expected)
        missing_total += sum(1 for item in expected if item not in produced)
    pending_review = sum(1 for artifact in artifacts if artifact.review_status == "pending")
    rejected = sum(1 for artifact in artifacts if artifact.review_status == "rejected")
    final_output_count = sum(1 for task in tasks if task.final_output is not None)
    return {
        "artifact_count": len(artifacts),
        "expected_artifact_count": expected_total,
        "missing_expected_artifact_count": missing_total,
        "pending_review_artifact_count": pending_review,
        "rejected_artifact_count": rejected,
        "final_output_task_count": final_output_count,
    }


def risk_items(
    task: dict[str, object],
    execution: dict[str, object],
    delivery: dict[str, object],
    control: dict[str, object],
) -> list[dict[str, object]]:
    risks: list[dict[str, object]] = []
    append_risk(
        risks,
        code="blocked_tasks",
        count=int_or_zero(task.get("blocked_task_count")),
        severity="high",
        recommended_action="inspect_project_dashboard",
    )
    append_risk(
        risks,
        code="failed_tasks",
        count=int_or_zero(task.get("failed_task_count")),
        severity="critical",
        recommended_action="create_corrections",
    )
    append_risk(
        risks,
        code="waiting_runtime_runs",
        count=int_or_zero(execution.get("waiting_runtime_run_count")),
        severity="high",
        recommended_action="inspect_runtime_capacity",
    )
    append_risk(
        risks,
        code="failed_runs",
        count=int_or_zero(execution.get("failed_run_count")),
        severity="high",
        recommended_action="retry_or_debug_runs",
    )
    append_risk(
        risks,
        code="missing_expected_artifacts",
        count=int_or_zero(delivery.get("missing_expected_artifact_count")),
        severity="high",
        recommended_action="create_corrections",
    )
    append_risk(
        risks,
        code="pending_artifact_review",
        count=int_or_zero(delivery.get("pending_review_artifact_count")),
        severity="medium",
        recommended_action="review_artifacts",
    )
    append_risk(
        risks,
        code="recent_control_activity",
        count=int_or_zero(control.get("control_message_count")),
        severity="low",
        recommended_action="inspect_control_diagnostics",
    )
    return risks


def append_risk(
    risks: list[dict[str, object]],
    *,
    code: str,
    count: int,
    severity: str,
    recommended_action: str,
) -> None:
    if count <= 0:
        return
    risks.append(
        {
            "code": code,
            "count": count,
            "severity": severity,
            "recommended_action": recommended_action,
        }
    )


def health_score(risks: list[dict[str, object]]) -> int:
    penalties = {
        "critical": 25,
        "high": 12,
        "medium": 6,
        "low": 2,
    }
    score = 100
    for risk in risks:
        severity = str(risk.get("severity") or "low")
        count = int_or_zero(risk.get("count"))
        score -= penalties.get(severity, 2) * count
    return max(0, min(score, 100))


def health_status(score: int, risks: list[dict[str, object]]) -> str:
    if any(risk.get("severity") == "critical" for risk in risks) or score < 50:
        return "critical"
    if score < 80:
        return "degraded"
    return "healthy"


def recommended_actions(risks: list[dict[str, object]]) -> list[str]:
    actions: list[str] = []
    for risk in risks:
        action = risk.get("recommended_action")
        if isinstance(action, str) and action not in actions:
            actions.append(action)
    return actions


def counts(values: object) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        output[str(value)] = output.get(str(value), 0) + 1
    return dict(sorted(output.items()))
