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

ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


class TeamProjectDashboardService:
    """Aggregate team tasks into a project-level delivery dashboard."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_dashboard(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        limit: int = 100,
    ) -> dict[str, object] | None:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return None

        task_statement = select(Task).where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id == team_id,
        )
        if not include_completed:
            task_statement = task_statement.where(~Task.status.in_(TERMINAL_TASK_STATUSES))
        tasks = list(
            self._session.scalars(
                task_statement.order_by(
                    Task.priority.desc(),
                    Task.updated_at.desc(),
                    Task.id.asc(),
                ).limit(limit)
            ).all()
        )
        task_ids = [task.id for task in tasks]
        steps_by_task = self._steps_by_task(workspace_id, task_ids)
        runs_by_task = self._runs_by_task(workspace_id, task_ids)
        artifacts_by_task = self._artifacts_by_task(workspace_id, task_ids)
        latest_messages = self._latest_messages(workspace_id, task_ids)
        items = [
            _task_item(
                task,
                steps=steps_by_task.get(task.id, []),
                runs=runs_by_task.get(task.id, []),
                artifacts=artifacts_by_task.get(task.id, []),
                latest_message=latest_messages.get(task.id),
            )
            for task in tasks
        ]
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "team": {
                "id": team.id,
                "name": team.name,
                "team_type": team.team_type,
                "status": team.status,
                "manager_agent_profile_id": team.manager_agent_profile_id,
                "runtime_space_id": team.runtime_space_id,
            },
            "summary": _summary(items, limit=limit, include_completed=include_completed),
            "tasks": items,
        }

    def _steps_by_task(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, list[TaskStep]]:
        if not task_ids:
            return {}
        steps = self._session.scalars(
            select(TaskStep)
            .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id.in_(task_ids))
            .order_by(TaskStep.order_index.asc(), TaskStep.id.asc())
        ).all()
        grouped: dict[UUID, list[TaskStep]] = defaultdict(list)
        for step in steps:
            grouped[step.task_id].append(step)
        return grouped

    def _runs_by_task(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, list[AgentRun]]:
        if not task_ids:
            return {}
        runs = self._session.scalars(
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id.in_(task_ids))
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        ).all()
        grouped: dict[UUID, list[AgentRun]] = defaultdict(list)
        for run in runs:
            if run.task_id is not None:
                grouped[run.task_id].append(run)
        return grouped

    def _artifacts_by_task(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, list[Artifact]]:
        if not task_ids:
            return {}
        artifacts = self._session.scalars(
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id, Artifact.task_id.in_(task_ids))
            .order_by(Artifact.created_at.asc(), Artifact.id.asc())
        ).all()
        grouped: dict[UUID, list[Artifact]] = defaultdict(list)
        for artifact in artifacts:
            if artifact.task_id is not None:
                grouped[artifact.task_id].append(artifact)
        return grouped

    def _latest_messages(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, TaskMessage]:
        if not task_ids:
            return {}
        messages = self._session.scalars(
            select(TaskMessage)
            .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id.in_(task_ids))
            .order_by(TaskMessage.sequence.asc(), TaskMessage.created_at.asc())
        ).all()
        latest: dict[UUID, TaskMessage] = {}
        for message in messages:
            latest[message.task_id] = message
        return latest


def _task_item(
    task: Task,
    *,
    steps: list[TaskStep],
    runs: list[AgentRun],
    artifacts: list[Artifact],
    latest_message: TaskMessage | None,
) -> dict[str, object]:
    step_counts = _step_status_counts(steps)
    run_counts = _run_status_counts(runs)
    delivery = _delivery_summary(task, steps, artifacts)
    blockers = _blocked_reasons(task, steps, delivery)
    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "domain_type": task.domain_type,
        "updated_at": task.updated_at,
        "progress": {
            "step_count": len(steps),
            "completed_step_count": step_counts.get("completed", 0),
            "completion_ratio": _completion_ratio(step_counts, len(steps)),
            "step_status_counts": step_counts,
        },
        "execution": {
            "active_run_count": sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES),
            "run_status_counts": run_counts,
            "latest_message": _latest_message_payload(latest_message),
        },
        "delivery": delivery,
        "blocked_reasons": blockers,
        "risk_level": _risk_level(task, blockers, delivery),
        "recommended_actions": _recommended_actions(task, blockers, delivery, runs),
    }


def _delivery_summary(
    task: Task,
    steps: list[TaskStep],
    artifacts: list[Artifact],
) -> dict[str, object]:
    artifacts_by_step: dict[UUID, list[Artifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.task_step_id is not None:
            artifacts_by_step[artifact.task_step_id].append(artifact)
    expected_total = 0
    missing_total = 0
    for step in steps:
        expected = [item for item in step.expected_artifacts if isinstance(item, str)]
        produced = {artifact.artifact_type for artifact in artifacts_by_step.get(step.id, [])}
        expected_total += len(expected)
        missing_total += sum(1 for item in expected if item not in produced)
    pending_review = sum(1 for artifact in artifacts if artifact.review_status == "pending")
    rejected = sum(1 for artifact in artifacts if artifact.review_status == "rejected")
    status = _delivery_status(
        task,
        artifact_count=len(artifacts),
        missing_expected=missing_total,
        pending_review=pending_review,
        rejected=rejected,
    )
    return {
        "status": status,
        "artifact_count": len(artifacts),
        "expected_artifact_count": expected_total,
        "missing_expected_artifact_count": missing_total,
        "pending_review_artifact_count": pending_review,
        "rejected_artifact_count": rejected,
        "has_final_output": task.final_output is not None,
    }


def _delivery_status(
    task: Task,
    *,
    artifact_count: int,
    missing_expected: int,
    pending_review: int,
    rejected: int,
) -> str:
    if task.status == "completed" and task.final_output is not None:
        return "accepted"
    if missing_expected > 0:
        return "incomplete"
    if rejected > 0:
        return "needs_revision"
    if pending_review > 0:
        return "needs_review"
    if task.final_output is not None:
        return "ready_to_finalize"
    if artifact_count > 0:
        return "ready_to_review"
    return "empty"


def _blocked_reasons(
    task: Task,
    steps: list[TaskStep],
    delivery: dict[str, object],
) -> list[str]:
    reasons: list[str] = []
    if task.status == "blocked":
        reasons.append("task_blocked")
    for step in steps:
        dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
        reason = dependencies.get("blocked_reason")
        if isinstance(reason, str) and reason:
            reasons.append(reason)
    if _int(delivery.get("missing_expected_artifact_count")) > 0:
        reasons.append("missing_expected_artifacts")
    if _int(delivery.get("rejected_artifact_count")) > 0:
        reasons.append("rejected_artifacts")
    return _dedupe(reasons)


def _recommended_actions(
    task: Task,
    blockers: list[str],
    delivery: dict[str, object],
    runs: list[AgentRun],
) -> list[str]:
    actions: list[str] = []
    if "task_paused" in blockers:
        actions.append("resume_task")
    if "missing_expected_artifacts" in blockers:
        actions.append("create_correction")
    if delivery.get("status") == "needs_review":
        actions.append("review_artifacts")
    if delivery.get("status") == "ready_to_review":
        actions.append("apply_delivery_decision")
    if delivery.get("status") == "ready_to_finalize":
        actions.append("finalize_task")
    if any(run.status in ACTIVE_RUN_STATUSES for run in runs):
        actions.append("monitor_active_runs")
    if not actions and task.status not in TERMINAL_TASK_STATUSES:
        actions.append("inspect_execution_status")
    return _dedupe(actions)


def _summary(
    items: list[dict[str, object]],
    *,
    limit: int,
    include_completed: bool,
) -> dict[str, object]:
    status_counts = _counts(str(item["status"]) for item in items)
    delivery_status_counts = _counts(
        str(_dict(item.get("delivery")).get("status")) for item in items
    )
    return {
        "total_tasks": len(items),
        "limit": limit,
        "include_completed": include_completed,
        "status_counts": status_counts,
        "delivery_status_counts": delivery_status_counts,
        "blocked_task_count": sum(1 for item in items if item.get("blocked_reasons")),
        "high_risk_task_count": sum(1 for item in items if item.get("risk_level") == "high"),
        "active_run_count": sum(
            _int(_dict(item.get("execution")).get("active_run_count")) for item in items
        ),
        "missing_expected_artifact_count": sum(
            _int(_dict(item.get("delivery")).get("missing_expected_artifact_count"))
            for item in items
        ),
        "pending_review_task_count": sum(
            1 for item in items if _dict(item.get("delivery")).get("status") == "needs_review"
        ),
        "ready_for_decision_task_count": sum(
            1 for item in items if _dict(item.get("delivery")).get("status") == "ready_to_review"
        ),
        "ready_to_finalize_task_count": sum(
            1
            for item in items
            if _dict(item.get("delivery")).get("status") == "ready_to_finalize"
        ),
    }


def _risk_level(
    task: Task,
    blockers: list[str],
    delivery: dict[str, object],
) -> str:
    if task.status in {"failed", "cancelled"} or blockers:
        return "high"
    if delivery.get("status") in {"needs_review", "ready_to_review", "ready_to_finalize"}:
        return "medium"
    if task.status in {"running", "blocked"}:
        return "medium"
    return "low"


def _latest_message_payload(message: TaskMessage | None) -> dict[str, object] | None:
    if message is None:
        return None
    return {
        "id": message.id,
        "sequence": message.sequence,
        "message_type": message.message_type,
        "created_at": message.created_at,
    }


def _step_status_counts(steps: list[TaskStep]) -> dict[str, int]:
    return _counts(step.status for step in steps)


def _run_status_counts(runs: list[AgentRun]) -> dict[str, int]:
    return _counts(run.status for run in runs)


def _completion_ratio(step_counts: dict[str, int], step_count: int) -> float:
    if step_count <= 0:
        return 0.0
    return round(step_counts.get("completed", 0) / step_count, 4)


def _counts(values: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result
