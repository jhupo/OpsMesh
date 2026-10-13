from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.orchestration.tasks.observation.cards import (
    build_artifact_cards,
    build_overview_cards,
    build_quality_cards,
    build_review_cards,
    build_timeline_cards,
)
from opsmesh.orchestration.tasks.observation.domain import build_domain_section
from opsmesh.orchestration.tasks.observation.models import SUPPORTED_VIEW_TYPES
from opsmesh.orchestration.tasks.observation.repository import TaskObservationRepository
from opsmesh.resources.artifacts.models import Artifact


class TaskObservationService:
    """Compose a task observation payload from existing durable task records."""

    def __init__(self, session: Session) -> None:
        self._repository = TaskObservationRepository(session)

    def get_observation(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        view_type: str | None = None,
    ) -> dict[str, object] | None:
        records = self._repository.get_records(workspace_id=workspace_id, task_id=task_id)
        if records is None:
            return None

        resolved_view_type = resolve_view_type(records.task, view_type)
        return {
            "workspace_id": records.task.workspace_id,
            "task_id": records.task.id,
            "view_type": resolved_view_type,
            "generated_at": datetime.now(UTC),
            "summary": build_summary(
                records.task,
                records.steps,
                records.runs,
                records.messages,
                records.artifacts,
            ),
            "sections": build_sections(
                task=records.task,
                view_type=resolved_view_type,
                steps=records.steps,
                runs=records.runs,
                messages=records.messages,
                artifacts=records.artifacts,
                run_events=records.run_events,
                agents=records.agents,
            ),
        }


def resolve_view_type(task: Task, requested: str | None) -> str:
    if requested is not None and requested != "auto":
        normalized = requested.strip().lower()
        if normalized not in SUPPORTED_VIEW_TYPES:
            raise ValueError("Unsupported observation view type")
        return normalized
    domain_type = (task.domain_type or "generic").strip().lower()
    return domain_type if domain_type in SUPPORTED_VIEW_TYPES else "generic"


def build_sections(
    *,
    task: Task,
    view_type: str,
    steps: list[TaskStep],
    runs: list[AgentRun],
    messages: list[TaskMessage],
    artifacts: list[Artifact],
    run_events: list[RunEvent],
    agents: dict[UUID, AgentProfile],
) -> list[dict[str, object]]:
    return [
        build_overview_section(task, steps, runs, agents),
        build_timeline_section(messages, run_events),
        build_artifact_section(artifacts),
        build_review_section(task, messages),
        build_quality_section(steps, messages),
        build_domain_section(task, view_type, artifacts),
    ]


def build_overview_section(
    task: Task,
    steps: list[TaskStep],
    runs: list[AgentRun],
    agents: dict[UUID, AgentProfile],
) -> dict[str, object]:
    return {
        "key": "overview",
        "title": "Overview",
        "cards": build_overview_cards(task, steps, runs, agents),
    }


def build_timeline_section(
    messages: list[TaskMessage],
    run_events: list[RunEvent],
) -> dict[str, object]:
    return {
        "key": "timeline",
        "title": "Timeline",
        "cards": build_timeline_cards(messages, run_events),
    }


def build_artifact_section(artifacts: list[Artifact]) -> dict[str, object]:
    return {
        "key": "artifacts",
        "title": "Artifacts",
        "cards": build_artifact_cards(artifacts),
    }


def build_review_section(task: Task, messages: list[TaskMessage]) -> dict[str, object]:
    return {
        "key": "review",
        "title": "Review",
        "cards": build_review_cards(task, messages),
    }


def build_quality_section(
    steps: list[TaskStep],
    messages: list[TaskMessage],
) -> dict[str, object]:
    return {
        "key": "quality",
        "title": "Quality",
        "cards": build_quality_cards(steps, messages),
    }


def build_summary(
    task: Task,
    steps: list[TaskStep],
    runs: list[AgentRun],
    messages: list[TaskMessage],
    artifacts: list[Artifact],
) -> dict[str, object]:
    step_counts = Counter(step.status for step in steps)
    run_counts = Counter(run.status for run in runs)
    completed_steps = step_counts.get("completed", 0)
    progress = completed_steps / len(steps) if steps else 0.0
    return {
        "title": task.title,
        "status": task.status,
        "domain_type": task.domain_type,
        "priority": task.priority,
        "progress": round(progress, 4),
        "step_counts": dict(sorted(step_counts.items())),
        "run_counts": dict(sorted(run_counts.items())),
        "message_count": len(messages),
        "artifact_count": len(artifacts),
        "has_final_output": task.final_output is not None,
    }
