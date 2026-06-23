from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import SchedulerPolicyResponse
from backend.app.operations.utils import positive_int_or_none
from backend.app.workspaces.models import Workspace


class SchedulerPolicyService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def scheduler_policy(self, workspace_id: UUID) -> SchedulerPolicyResponse:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        raw_scheduler = settings.get("scheduler") if isinstance(settings, dict) else None
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        return SchedulerPolicyResponse(
            paused=scheduler.get("paused") is True,
            pause_reason=non_empty_string_or_none(scheduler.get("pause_reason")),
            max_active_runs=positive_int_or_none(scheduler.get("max_active_runs")),
            max_running_tasks=positive_int_or_none(scheduler.get("max_running_tasks")),
            max_runs_to_start_per_tick=positive_int_or_none(
                scheduler.get("max_runs_to_start_per_tick")
            ),
            max_steps_per_task_per_tick=positive_int_or_none(
                scheduler.get("max_steps_per_task_per_tick")
            ),
            starvation_boost_after_seconds=positive_int_or_none(
                scheduler.get("starvation_boost_after_seconds")
            ),
            resource_limits=positive_number_dict(scheduler.get("resource_limits")),
        )


def scheduler_settings(settings: dict[str, object]) -> dict[str, object]:
    raw_scheduler = settings.get("scheduler")
    if not isinstance(raw_scheduler, dict):
        return {}
    return dict(raw_scheduler)


def non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def positive_number_dict(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, item in value.items():
        if isinstance(item, int | float) and not isinstance(item, bool) and item > 0:
            normalized[str(key)] = float(item)
    return normalized
