"""Build operator queues from the same bounded task window as the overview."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.collaboration.contracts import ManagerDiagnostics, ManagerQueueItem
from opsmesh.orchestration.tasks.collaboration.handoff import (
    handoff_needs_attention,
    handoff_queue_item,
    handoff_queue_summary,
)
from opsmesh.orchestration.tasks.collaboration.manager_queue import (
    manager_queue_item,
    manager_queue_summary,
)
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.orchestration.tasks.observation.execution import TaskExecutionDiagnosticsService
from opsmesh.shared.utils import dict_list, dict_or_empty


def command_center_queues(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
    overview: dict[str, object],
    limit: int,
    manager_diagnostics: dict[UUID, ManagerDiagnostics],
    tasks: list[Task],
    steps: list[TaskStep],
    runs: list[AgentRun],
) -> tuple[dict[str, object], dict[str, object]]:
    if any(task.workspace_id != workspace_id or task.agent_team_id != team_id for task in tasks):
        raise ValueError("Queue task outside team scope")
    handoffs: list[dict[str, object]] = []
    managers: list[ManagerQueueItem] = []
    diagnostics_by_task = TaskExecutionDiagnosticsService(session).diagnostics_for_tasks(
        workspace_id=workspace_id, tasks=tasks, steps=steps, runs=runs
    )
    for task in tasks:
        manager = manager_diagnostics.get(task.id)
        if manager is not None:
            item = manager_queue_item(task, manager)
            if item["needs_attention"]:
                managers.append(item)
        diagnostics = diagnostics_by_task.get(task.id)
        if diagnostics is not None:
            for step in dict_list(diagnostics.get("steps")):
                handoff = handoff_queue_item(task, step)
                if handoff is not None and handoff_needs_attention(handoff):
                    handoffs.append(handoff)
    common = {
        "workspace_id": workspace_id,
        "team_id": team_id,
        "generated_at": datetime.now(UTC),
        "limit": limit,
        "offset": 0,
        "scanned_task_count": len(tasks),
        "total_is_complete": dict_or_empty(overview.get("summary")).get("tasks_truncated") is False,
    }
    return (
        {
            **common,
            "total": len(handoffs),
            "summary": handoff_queue_summary(handoffs),
            "items": handoffs[:limit],
        },
        {
            **common,
            "total": len(managers),
            "summary": manager_queue_summary(managers),
            "items": managers[:limit],
        },
    )
