from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.artifacts.models import Artifact
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam
from backend.app.workspaces.models import Workspace, WorkspaceHealthSnapshot

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}


@dataclass(frozen=True)
class WorkspaceHealthSnapshotMaintenanceSummary:
    workspaces_scanned: int = 0
    snapshots_created: int = 0
    snapshots_skipped: int = 0
    snapshots_disabled: int = 0


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

    def get_trends(self, workspace_id: UUID, *, limit: int = 20) -> dict[str, object]:
        snapshots, total = self.list_snapshots(workspace_id, limit=limit, offset=0)
        latest = snapshots[0] if snapshots else None
        previous = snapshots[1] if len(snapshots) > 1 else None
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "snapshot_count": total,
            "compared_snapshot_count": len(snapshots),
            "latest": _snapshot_payload(latest),
            "previous": _snapshot_payload(previous),
            "score_delta": _score_delta(latest, previous),
            "status_change": _status_change(latest, previous),
            "risk_changes": _risk_changes(latest, previous),
            "recommendation_changes": _recommendation_changes(latest, previous),
        }

    def run_scheduled_snapshots(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> WorkspaceHealthSnapshotMaintenanceSummary:
        current_time = _aware(now or datetime.now(UTC))
        workspaces = list(
            self._session.scalars(
                select(Workspace)
                .where(Workspace.status == "active")
                .order_by(Workspace.created_at.asc(), Workspace.id.asc())
                .limit(max(1, limit))
            ).all()
        )
        created = 0
        skipped = 0
        disabled = 0
        for workspace in workspaces:
            policy = _health_snapshot_policy(workspace.settings)
            if policy is None:
                disabled += 1
                continue
            latest = self._latest_snapshot(workspace.id)
            if latest is not None and not _snapshot_due(
                latest.created_at,
                current_time,
                policy["interval"],
            ):
                skipped += 1
                continue
            self.record_snapshot(workspace.id)
            created += 1
        return WorkspaceHealthSnapshotMaintenanceSummary(
            workspaces_scanned=len(workspaces),
            snapshots_created=created,
            snapshots_skipped=skipped,
            snapshots_disabled=disabled,
        )

    def _latest_snapshot(self, workspace_id: UUID) -> WorkspaceHealthSnapshot | None:
        return self._session.scalar(
            select(WorkspaceHealthSnapshot)
            .where(WorkspaceHealthSnapshot.workspace_id == workspace_id)
            .order_by(
                WorkspaceHealthSnapshot.created_at.desc(),
                WorkspaceHealthSnapshot.id.desc(),
            )
            .limit(1)
        )

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


def _health_snapshot_policy(settings: object) -> dict[str, timedelta] | None:
    root = _dict(settings)
    operations = _dict(root.get("operations"))
    policy = _dict(operations.get("health_snapshots"))
    if not policy:
        policy = _dict(root.get("health_snapshots"))
    if policy.get("enabled") is not True:
        return None
    interval = _positive_timedelta(policy.get("interval_minutes"), unit="minutes")
    if interval is None:
        interval = _positive_timedelta(policy.get("interval_hours"), unit="hours")
    return {"interval": interval or timedelta(hours=24)}


def _positive_timedelta(value: object, *, unit: str) -> timedelta | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value > 0:
        return timedelta(**{unit: float(value)})
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        if parsed > 0:
            return timedelta(**{unit: parsed})
    return None


def _snapshot_due(
    latest_created_at: datetime,
    now: datetime,
    interval: timedelta,
) -> bool:
    return _aware(latest_created_at) <= now - interval


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _snapshot_payload(snapshot: WorkspaceHealthSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "id": snapshot.id,
        "created_at": snapshot.created_at,
        "status": snapshot.status,
        "score": snapshot.score,
        "risk_count": len(snapshot.risk_items or []),
        "recommended_action_count": len(snapshot.recommended_actions or []),
    }


def _score_delta(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> int | None:
    if latest is None or previous is None:
        return None
    return latest.score - previous.score


def _status_change(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> dict[str, object] | None:
    if latest is None or previous is None:
        return None
    return {
        "from": previous.status,
        "to": latest.status,
        "changed": previous.status != latest.status,
        "direction": _status_direction(previous.status, latest.status),
    }


def _status_direction(previous: str, latest: str) -> str:
    rank = {"critical": 0, "degraded": 1, "healthy": 2}
    previous_rank = rank.get(previous, 1)
    latest_rank = rank.get(latest, 1)
    if latest_rank > previous_rank:
        return "improved"
    if latest_rank < previous_rank:
        return "worsened"
    return "unchanged"


def _risk_changes(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> dict[str, object]:
    latest_risks = _risk_counts(latest)
    previous_risks = _risk_counts(previous)
    codes = sorted(set(latest_risks) | set(previous_risks))
    changes = [
        {
            "code": code,
            "previous_count": previous_risks.get(code, 0),
            "latest_count": latest_risks.get(code, 0),
            "delta": latest_risks.get(code, 0) - previous_risks.get(code, 0),
        }
        for code in codes
    ]
    return {
        "resolved": [
            item
            for item in changes
            if item["previous_count"] > 0 and item["latest_count"] == 0
        ],
        "new": [
            item
            for item in changes
            if item["previous_count"] == 0 and item["latest_count"] > 0
        ],
        "improved": [item for item in changes if item["delta"] < 0 and item["latest_count"] > 0],
        "worsened": [item for item in changes if item["delta"] > 0 and item["previous_count"] > 0],
        "all": changes,
    }


def _risk_counts(snapshot: WorkspaceHealthSnapshot | None) -> dict[str, int]:
    if snapshot is None:
        return {}
    counts: dict[str, int] = {}
    for item in snapshot.risk_items or []:
        code = item.get("code")
        if not isinstance(code, str):
            continue
        counts[code] = _int(item.get("count"))
    return counts


def _recommendation_changes(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> dict[str, list[str]]:
    latest_actions = set(latest.recommended_actions if latest is not None else [])
    previous_actions = set(previous.recommended_actions if previous is not None else [])
    return {
        "added": sorted(latest_actions - previous_actions),
        "removed": sorted(previous_actions - latest_actions),
        "active": sorted(latest_actions),
    }
