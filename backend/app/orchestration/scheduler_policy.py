from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.orchestration.resource_usage import scheduler_numeric_limits
from backend.app.workspaces.models import Workspace


@dataclass(frozen=True)
class WorkspaceSchedulerPolicy:
    paused: bool = False
    pause_reason: str | None = None
    max_active_runs: int | None = None
    max_running_tasks: int | None = None
    max_runs_to_start_per_tick: int | None = None
    max_steps_per_task_per_tick: int = 1
    starvation_boost_after_seconds: int | None = None
    resource_limits: dict[str, float] | None = None


class SchedulerPolicyResolver:
    def __init__(self, session: Session) -> None:
        self._session = session

    def policy_for(
        self,
        workspace_id: UUID,
        *,
        override: dict[str, object] | None = None,
    ) -> WorkspaceSchedulerPolicy:
        workspace = self._session.get(Workspace, workspace_id)
        raw_settings = workspace.settings if workspace is not None else {}
        raw_scheduler = raw_settings.get("scheduler") if isinstance(raw_settings, dict) else None
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        effective_scheduler = merged_scheduler_policy(scheduler, override)
        return WorkspaceSchedulerPolicy(
            paused=scheduler.get("paused") is True,
            pause_reason=non_empty_string_or_none(scheduler.get("pause_reason")),
            max_active_runs=positive_int_or_none(effective_scheduler.get("max_active_runs")),
            max_running_tasks=positive_int_or_none(effective_scheduler.get("max_running_tasks")),
            max_runs_to_start_per_tick=positive_int_or_none(
                effective_scheduler.get("max_runs_to_start_per_tick")
            ),
            max_steps_per_task_per_tick=positive_int_or_default(
                effective_scheduler.get("max_steps_per_task_per_tick"),
                1,
            ),
            starvation_boost_after_seconds=positive_int_or_none(
                effective_scheduler.get("starvation_boost_after_seconds")
            ),
            resource_limits=scheduler_numeric_limits(effective_scheduler.get("resource_limits")),
        )


def merged_scheduler_policy(
    workspace_scheduler: dict[str, object],
    override: dict[str, object] | None,
) -> dict[str, object]:
    if not override:
        return workspace_scheduler
    effective = dict(workspace_scheduler)
    for key in (
        "max_active_runs",
        "max_running_tasks",
        "max_runs_to_start_per_tick",
        "max_steps_per_task_per_tick",
        "starvation_boost_after_seconds",
        "resource_limits",
    ):
        if key in override:
            effective[key] = override[key]
    return effective


def positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def positive_int_or_default(value: object, default: int) -> int:
    parsed = positive_int_or_none(value)
    return parsed if parsed is not None else default


def non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
