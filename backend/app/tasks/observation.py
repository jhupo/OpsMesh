from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.tasks.models import Task
from backend.app.tasks.observation_models import SUPPORTED_VIEW_TYPES
from backend.app.tasks.observation_repository import TaskObservationRepository
from backend.app.tasks.observation_sections import TaskObservationSectionBuilder


class TaskObservationService:
    """Compose a task observation payload from existing durable task records."""

    def __init__(self, session: Session) -> None:
        self._repository = TaskObservationRepository(session)
        self._sections = TaskObservationSectionBuilder()

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
            "summary": self._sections.summary(
                records.task,
                records.steps,
                records.runs,
                records.messages,
                records.artifacts,
            ),
            "sections": self._sections.sections(
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
