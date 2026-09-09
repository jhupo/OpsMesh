from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.typing import dict_or_empty, int_or_zero, string_list
from backend.app.tasks.control_state import task_control_state
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.live_status import TaskLiveStatusService
from backend.app.tasks.manager_contracts import ManagerDiagnostics
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.tasks.models import Task
from backend.app.tasks.timeline import TaskTimelineService


class TaskExecutionStatusService:
    """Compose live task execution state for an operator-facing control panel."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_status(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        message_limit: int = 20,
        event_limit: int = 30,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        live = TaskLiveStatusService(self._session).get_status(
            workspace_id=workspace_id,
            task_id=task_id,
            message_limit=message_limit,
        )
        execution = TaskExecutionDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        manager = TaskManagerDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        timeline = TaskTimelineService(self._session).get_timeline(
            workspace_id=workspace_id,
            task_id=task_id,
            limit=event_limit,
        )
        if live is None or execution is None or manager is None or timeline is None:
            return None

        live_summary = dict_or_empty(live.get("summary"))
        execution_summary = dict_or_empty(execution.get("summary"))
        manager_summary = dict_or_empty(manager.get("summary"))
        control = task_control_state(task)
        blocked_reasons = _blocked_reasons(
            control=control,
            execution=execution,
            manager=manager,
        )
        recommended_actions = _recommended_actions(
            control=control,
            live=live,
            execution_summary=execution_summary,
            manager=manager,
            blocked_reasons=blocked_reasons,
        )
        current_focus = _current_focus(
            live=live,
            execution=execution,
            blocked_reasons=blocked_reasons,
        )
        active_runs = _list(live.get("active_runs"))
        recent_messages = _list(live.get("recent_messages"))[:message_limit]
        recent_events = _list(timeline.get("events"))[-event_limit:]

        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "status": _overall_status(
                task_status=task.status,
                control=control,
                active_run_count=int_or_zero(live_summary.get("active_run_count")),
                blocked_reasons=blocked_reasons,
            ),
            "task": live.get("task"),
            "control": control,
            "summary": {
                "task_status": task.status,
                "step_status_counts": live_summary.get("step_status_counts", {}),
                "run_status_counts": live_summary.get("run_status_counts", {}),
                "active_run_count": int_or_zero(live_summary.get("active_run_count")),
                "runnable_step_count": int_or_zero(execution_summary.get("runnable_steps")),
                "blocked_step_count": int_or_zero(execution_summary.get("blocked_steps")),
                "manager_status": manager_summary.get("status"),
                "latest_message_sequence": int_or_zero(live_summary.get("latest_message_sequence")),
                "poll_after_seconds": int_or_zero(live_summary.get("poll_after_seconds")),
                "blocked_reason_count": len(blocked_reasons),
                "recommended_action_count": len(recommended_actions),
            },
            "current_focus": current_focus,
            "active_runs": active_runs,
            "blocked_reasons": blocked_reasons,
            "recommended_actions": recommended_actions,
            "recent_messages": recent_messages,
            "recent_events": recent_events,
            "diagnostics": {
                "live_summary": live_summary,
                "execution_summary": execution_summary,
                "manager_summary": manager_summary,
                "timeline_summary": dict_or_empty(timeline.get("summary")),
            },
        }


def _overall_status(
    *,
    task_status: str,
    control: dict[str, object],
    active_run_count: int,
    blocked_reasons: list[str],
) -> str:
    if control.get("paused") is True:
        return "paused"
    if task_status in {"completed", "failed", "cancelled"}:
        return task_status
    if blocked_reasons:
        return "blocked"
    if active_run_count > 0:
        return "active"
    return "waiting"


def _current_focus(
    *,
    live: dict[str, object],
    execution: dict[str, object],
    blocked_reasons: list[str],
) -> dict[str, object]:
    active_runs = _list(live.get("active_runs"))
    if active_runs:
        run = dict_or_empty(active_runs[0])
        latest_event = dict_or_empty(run.get("latest_event"))
        return {
            "kind": "run",
            "status": run.get("status"),
            "run_id": run.get("id"),
            "task_step_id": run.get("task_step_id"),
            "agent": run.get("agent"),
            "activity": dict_or_empty(run.get("activity")) or None,
            "latest_event": latest_event or None,
        }

    steps = _list(execution.get("steps"))
    runnable = next((step for step in steps if dict_or_empty(step).get("runnable") is True), None)
    if isinstance(runnable, dict):
        return _step_focus("step", runnable)
    blocked = next((step for step in steps if dict_or_empty(step).get("blocked_reasons")), None)
    if isinstance(blocked, dict):
        return _step_focus("blocked_step", blocked)
    return {
        "kind": "task",
        "status": "blocked" if blocked_reasons else "waiting",
        "blocked_reasons": blocked_reasons,
    }


def _step_focus(kind: str, step: dict[str, object]) -> dict[str, object]:
    return {
        "kind": kind,
        "status": step.get("status"),
        "task_step_id": step.get("task_step_id"),
        "work_package_id": step.get("work_package_id"),
        "title": step.get("title"),
        "assigned_agent": step.get("assigned_agent"),
        "blocked_reasons": string_list(step.get("blocked_reasons")),
    }


def _blocked_reasons(
    *,
    control: dict[str, object],
    execution: dict[str, object],
    manager: ManagerDiagnostics,
) -> list[str]:
    reasons: list[str] = []
    if control.get("paused") is True:
        reasons.append("task_paused")
    reasons.extend(string_list(manager.get("blocked_reasons")))
    for step in _list(execution.get("steps")):
        step_payload = dict_or_empty(step)
        reasons.extend(string_list(step_payload.get("blocked_reasons")))
    return _dedupe(reasons)


def _recommended_actions(
    *,
    control: dict[str, object],
    live: dict[str, object],
    execution_summary: dict[str, object],
    manager: ManagerDiagnostics,
    blocked_reasons: list[str],
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if control.get("paused") is True:
        actions.append(
            {
                "action": "resume",
                "reason": "task_paused",
                "api_route": "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/control",
                "payload_template": {"action": "resume"},
            }
        )
    active_runs = _list(live.get("active_runs"))
    if active_runs:
        statuses = {str(dict_or_empty(run).get("status")) for run in active_runs}
        if "waiting_approval" in statuses:
            actions.append({"action": "review_approval", "reason": "run_waiting_approval"})
        if "waiting_runtime" in statuses:
            actions.append({"action": "inspect_runtime", "reason": "run_waiting_runtime"})
    if int_or_zero(execution_summary.get("runnable_steps")) > 0 and not active_runs:
        actions.append(
            {
                "action": "resume_or_schedule_work",
                "reason": "runnable_steps_without_active_run",
                "api_route": "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/control",
                "payload_template": {"action": "resume", "enqueue": True},
            }
        )
    if any("scheduler:" in reason for reason in blocked_reasons):
        actions.append({"action": "inspect_scheduler_block", "reason": "scheduler_blocked_step"})

    manager_actions = _manager_recommended_actions(manager)
    for action in manager_actions:
        actions.append({"action": action, "reason": "manager_diagnostics"})
    return _dedupe_action_items(actions)


def _manager_recommended_actions(manager: ManagerDiagnostics) -> list[str]:
    reasons = set(string_list(manager.get("blocked_reasons")))
    actions: list[str] = []
    if "missing_manager" in reasons:
        actions.append("assign_manager")
    if reasons & {
        "missing_manager_planning_step",
        "missing_manager_summary_step",
        "acceptance_decision_missing",
        "follow_up_missing",
    }:
        actions.append("request_manager_review")
    if "follow_up_incomplete" in reasons:
        actions.append("monitor_follow_up")
    if "specialist_steps_incomplete" in reasons:
        actions.append("monitor_specialists")
    return actions


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def _dedupe_action_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for item in items:
        action = item.get("action")
        if not isinstance(action, str) or action in seen:
            continue
        result.append(item)
        seen.add(action)
    return result
