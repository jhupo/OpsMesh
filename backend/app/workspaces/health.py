from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam
from backend.app.workspaces.models import WorkspaceHealthSnapshot

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}


class WorkspaceHealthService:
    """Compute workspace-level product operations health from current state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_health(self, workspace_id: UUID) -> dict[str, object]:
        tasks = list(
            self._session.scalars(
                select(Task)
                .where(Task.workspace_id == workspace_id)
                .order_by(Task.updated_at.desc(), Task.id.asc())
            ).all()
        )
        task_ids = [task.id for task in tasks]
        steps = self._steps(workspace_id, task_ids)
        runs = self._runs(workspace_id, task_ids)
        artifacts = self._artifacts(workspace_id, task_ids)
        control_messages = self._control_messages(workspace_id, task_ids)
        team_count = self._team_count(workspace_id)
        delivery = _delivery_summary(tasks, steps, artifacts)
        execution = _execution_summary(runs)
        task_summary = _task_summary(tasks)
        control = {
            "control_message_count": len(control_messages),
            "latest_control_sequence": max(
                (message.sequence for message in control_messages),
                default=0,
            ),
        }
        risks = _risk_items(task_summary, execution, delivery, control)
        score = _health_score(risks)
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "status": _health_status(score, risks),
            "score": score,
            "summary": {
                "team_count": team_count,
                **task_summary,
                **execution,
                **delivery,
                **control,
            },
            "risk_items": risks,
            "recommended_actions": _recommended_actions(risks),
            "trend_basis": {
                "mode": "snapshot",
                "message": "Use health snapshots to persist and compare this score over time.",
            },
        }

    def record_snapshot(self, workspace_id: UUID) -> WorkspaceHealthSnapshot:
        health = self.get_health(workspace_id)
        snapshot = WorkspaceHealthSnapshot(
            workspace_id=workspace_id,
            status=str(health["status"]),
            score=int(health["score"]),
            summary=_dict(health.get("summary")),
            risk_items=_dict_list(health.get("risk_items")),
            recommended_actions=_string_list(health.get("recommended_actions")),
            trend_basis={
                **_dict(health.get("trend_basis")),
                "mode": "persisted_snapshot",
            },
        )
        self._session.add(snapshot)
        self._session.commit()
        self._session.refresh(snapshot)
        return snapshot

    def list_snapshots(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WorkspaceHealthSnapshot], int]:
        statement = (
            select(WorkspaceHealthSnapshot)
            .where(WorkspaceHealthSnapshot.workspace_id == workspace_id)
            .order_by(WorkspaceHealthSnapshot.created_at.desc(), WorkspaceHealthSnapshot.id.desc())
        )
        total = len(
            self._session.scalars(
                select(WorkspaceHealthSnapshot.id).where(
                    WorkspaceHealthSnapshot.workspace_id == workspace_id
                )
            ).all()
        )
        items = list(self._session.scalars(statement.limit(limit).offset(offset)).all())
        return items, total

    def _team_count(self, workspace_id: UUID) -> int:
        teams = self._session.scalars(
            select(AgentTeam.id).where(AgentTeam.workspace_id == workspace_id)
        ).all()
        return len(teams)

    def _steps(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskStep]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskStep).where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.task_id.in_(task_ids),
                )
            ).all()
        )

    def _runs(self, workspace_id: UUID, task_ids: list[UUID]) -> list[AgentRun]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.task_id.in_(task_ids),
                )
            ).all()
        )

    def _artifacts(self, workspace_id: UUID, task_ids: list[UUID]) -> list[Artifact]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(Artifact).where(
                    Artifact.workspace_id == workspace_id,
                    Artifact.task_id.in_(task_ids),
                )
            ).all()
        )

    def _control_messages(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskMessage]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id.in_(task_ids),
                    TaskMessage.message_type.like("task.control.%"),
                )
                .order_by(TaskMessage.created_at.desc(), TaskMessage.id.asc())
            ).all()
        )


def _task_summary(tasks: list[Task]) -> dict[str, object]:
    status_counts = _counts(task.status for task in tasks)
    active_tasks = sum(1 for task in tasks if task.status in {"queued", "running", "blocked"})
    return {
        "task_count": len(tasks),
        "active_task_count": active_tasks,
        "blocked_task_count": status_counts.get("blocked", 0),
        "failed_task_count": status_counts.get("failed", 0),
        "completed_task_count": status_counts.get("completed", 0),
        "task_status_counts": status_counts,
    }


def _execution_summary(runs: list[AgentRun]) -> dict[str, object]:
    status_counts = _counts(run.status for run in runs)
    return {
        "run_count": len(runs),
        "active_run_count": sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES),
        "waiting_runtime_run_count": status_counts.get(RunStatus.WAITING_RUNTIME.value, 0),
        "failed_run_count": status_counts.get(RunStatus.FAILED.value, 0),
        "run_status_counts": status_counts,
    }


def _delivery_summary(
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


def _risk_items(
    task_summary: dict[str, object],
    execution: dict[str, object],
    delivery: dict[str, object],
    control: dict[str, object],
) -> list[dict[str, object]]:
    risks: list[dict[str, object]] = []
    _append_risk(
        risks,
        code="blocked_tasks",
        count=_int(task_summary.get("blocked_task_count")),
        severity="high",
        recommended_action="inspect_project_dashboard",
    )
    _append_risk(
        risks,
        code="failed_tasks",
        count=_int(task_summary.get("failed_task_count")),
        severity="critical",
        recommended_action="create_corrections",
    )
    _append_risk(
        risks,
        code="waiting_runtime_runs",
        count=_int(execution.get("waiting_runtime_run_count")),
        severity="high",
        recommended_action="inspect_runtime_capacity",
    )
    _append_risk(
        risks,
        code="failed_runs",
        count=_int(execution.get("failed_run_count")),
        severity="high",
        recommended_action="retry_or_debug_runs",
    )
    _append_risk(
        risks,
        code="missing_expected_artifacts",
        count=_int(delivery.get("missing_expected_artifact_count")),
        severity="high",
        recommended_action="create_corrections",
    )
    _append_risk(
        risks,
        code="pending_artifact_review",
        count=_int(delivery.get("pending_review_artifact_count")),
        severity="medium",
        recommended_action="review_artifacts",
    )
    _append_risk(
        risks,
        code="recent_control_activity",
        count=_int(control.get("control_message_count")),
        severity="low",
        recommended_action="inspect_control_diagnostics",
    )
    return risks


def _append_risk(
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


def _health_score(risks: list[dict[str, object]]) -> int:
    penalties = {
        "critical": 25,
        "high": 12,
        "medium": 6,
        "low": 2,
    }
    score = 100
    for risk in risks:
        severity = str(risk.get("severity") or "low")
        count = _int(risk.get("count"))
        score -= penalties.get(severity, 2) * count
    return max(0, min(score, 100))


def _health_status(score: int, risks: list[dict[str, object]]) -> str:
    if any(risk.get("severity") == "critical" for risk in risks) or score < 50:
        return "critical"
    if score < 80:
        return "degraded"
    return "healthy"


def _recommended_actions(risks: list[dict[str, object]]) -> list[str]:
    actions: list[str] = []
    for risk in risks:
        action = risk.get("recommended_action")
        if isinstance(action, str) and action not in actions:
            actions.append(action)
    return actions


def _counts(values: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
