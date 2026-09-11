from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.core.typing import int_or_zero, string_list
from backend.app.files.artifact_models import Artifact
from backend.app.tasks.models import Task, TaskStep


class TaskDeliveryReviewService:
    """Assemble delivery readiness and artifact review state for a task."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_review(self, *, workspace_id: UUID, task_id: UUID) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.id.asc())
            ).all()
        )
        artifacts = list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == workspace_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.asc(), Artifact.version.asc(), Artifact.id.asc())
            ).all()
        )
        agents = self._agents(workspace_id, artifacts)
        artifacts_by_step_id = _artifacts_by_step_id(artifacts)
        artifact_type_counts = _artifact_type_counts(artifacts)
        step_reviews = [
            _step_review(
                step,
                artifacts=artifacts_by_step_id.get(step.id, []),
                agents=agents,
            )
            for step in steps
        ]
        unattached_artifacts = [
            _artifact_summary(
                artifact,
                agents.get(artifact.agent_profile_id) if artifact.agent_profile_id else None,
            )
            for artifact in artifacts
            if artifact.task_step_id is None
        ]
        summary = _summary(task, step_reviews, artifacts, artifact_type_counts)
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "status": _delivery_status(task, summary),
            "task": {
                "title": task.title,
                "status": task.status,
                "domain_type": task.domain_type,
                "priority": task.priority,
                "completed_at": task.completed_at,
            },
            "summary": summary,
            "final_output": task.final_output,
            "steps": step_reviews,
            "unattached_artifacts": unattached_artifacts,
            "recommended_actions": _recommended_actions(task, summary),
        }

    def _agents(
        self,
        workspace_id: UUID,
        artifacts: list[Artifact],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            artifact.agent_profile_id for artifact in artifacts if artifact.agent_profile_id
        }
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}


def _step_review(
    step: TaskStep,
    *,
    artifacts: list[Artifact],
    agents: dict[UUID, AgentProfile],
) -> dict[str, object]:
    expected = list(step.expected_artifacts or [])
    produced_types = {artifact.artifact_type for artifact in artifacts}
    missing = [item for item in expected if item not in produced_types]
    latest_artifacts = [
        _artifact_summary(
            artifact, agents.get(artifact.agent_profile_id) if artifact.agent_profile_id else None
        )
        for artifact in _latest_artifacts_by_type(artifacts)
    ]
    return {
        "task_step_id": step.id,
        "work_package_id": step.work_package_id,
        "title": step.title,
        "status": step.status,
        "expected_artifacts": expected,
        "produced_artifact_types": sorted(produced_types),
        "missing_expected_artifacts": missing,
        "artifact_count": len(artifacts),
        "latest_artifacts": latest_artifacts,
        "review_status_counts": _review_status_counts(artifacts),
        "result_summary": step.result_summary,
    }


def _artifact_summary(
    artifact: Artifact,
    agent: AgentProfile | None,
) -> dict[str, object]:
    return {
        "id": artifact.id,
        "agent_run_id": artifact.agent_run_id,
        "task_step_id": artifact.task_step_id,
        "agent_profile_id": artifact.agent_profile_id,
        "agent": _agent_summary(agent),
        "work_package_id": artifact.work_package_id,
        "version": artifact.version,
        "review_status": artifact.review_status,
        "artifact_type": artifact.artifact_type,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "supersedes_artifact_id": artifact.supersedes_artifact_id,
        "metadata": artifact.artifact_metadata,
        "created_at": artifact.created_at,
    }


def _summary(
    task: Task,
    step_reviews: list[dict[str, object]],
    artifacts: list[Artifact],
    artifact_type_counts: dict[str, int],
) -> dict[str, object]:
    expected_total = sum(len(string_list(step.get("expected_artifacts"))) for step in step_reviews)
    missing_total = sum(
        len(string_list(step.get("missing_expected_artifacts"))) for step in step_reviews
    )
    pending_review = sum(1 for artifact in artifacts if artifact.review_status == "pending")
    rejected = sum(1 for artifact in artifacts if artifact.review_status == "rejected")
    approved = sum(1 for artifact in artifacts if artifact.review_status == "approved")
    return {
        "task_status": task.status,
        "step_count": len(step_reviews),
        "completed_step_count": sum(
            1 for step in step_reviews if step.get("status") == "completed"
        ),
        "artifact_count": len(artifacts),
        "expected_artifact_count": expected_total,
        "produced_expected_artifact_count": max(expected_total - missing_total, 0),
        "missing_expected_artifact_count": missing_total,
        "pending_review_artifact_count": pending_review,
        "approved_artifact_count": approved,
        "rejected_artifact_count": rejected,
        "has_final_output": task.final_output is not None,
        "artifact_type_counts": artifact_type_counts,
    }


def _delivery_status(task: Task, summary: dict[str, object]) -> str:
    if int_or_zero(summary.get("missing_expected_artifact_count")) > 0:
        return "incomplete"
    if int_or_zero(summary.get("rejected_artifact_count")) > 0:
        return "needs_revision"
    if int_or_zero(summary.get("pending_review_artifact_count")) > 0:
        return "needs_review"
    if task.final_output is not None:
        return "accepted" if task.status == "completed" else "ready_to_finalize"
    if int_or_zero(summary.get("artifact_count")) > 0:
        return "ready_to_review"
    return "empty"


def _recommended_actions(task: Task, summary: dict[str, object]) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if int_or_zero(summary.get("missing_expected_artifact_count")) > 0:
        actions.append(
            {
                "action": "create_correction",
                "reason": "missing_expected_artifacts",
                "api_route": "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/control",
                "payload_template": {
                    "action": "create_correction",
                    "correction_mode": "add_missing_work",
                    "target_type": "task",
                },
            }
        )
    if int_or_zero(summary.get("rejected_artifact_count")) > 0:
        actions.append({"action": "request_revision", "reason": "rejected_artifacts"})
    if int_or_zero(summary.get("pending_review_artifact_count")) > 0:
        actions.append({"action": "review_artifacts", "reason": "pending_artifact_review"})
    if (
        task.final_output is None
        and int_or_zero(summary.get("missing_expected_artifact_count")) == 0
    ):
        actions.append({"action": "request_manager_review", "reason": "final_output_missing"})
    return actions


def _artifacts_by_step_id(artifacts: list[Artifact]) -> dict[UUID, list[Artifact]]:
    by_step: dict[UUID, list[Artifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.task_step_id is None:
            continue
        by_step[artifact.task_step_id].append(artifact)
    return by_step


def _latest_artifacts_by_type(artifacts: list[Artifact]) -> list[Artifact]:
    latest: dict[str, Artifact] = {}
    for artifact in artifacts:
        current = latest.get(artifact.artifact_type)
        if current is None or (artifact.version, artifact.created_at, artifact.id) > (
            current.version,
            current.created_at,
            current.id,
        ):
            latest[artifact.artifact_type] = artifact
    return sorted(latest.values(), key=lambda item: (item.artifact_type, item.version))


def _review_status_counts(artifacts: list[Artifact]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for artifact in artifacts:
        counts[artifact.review_status] = counts.get(artifact.review_status, 0) + 1
    return counts


def _artifact_type_counts(artifacts: list[Artifact]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for artifact in artifacts:
        counts[artifact.artifact_type] = counts.get(artifact.artifact_type, 0) + 1
    return counts


def _agent_summary(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }
