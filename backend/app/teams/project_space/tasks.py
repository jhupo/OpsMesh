from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from backend.app.artifacts.models import Artifact
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.project_space.policies import ACTIVE_RUN_STATUSES
from backend.app.teams.project_space.types import ProjectSpaceRecords
from backend.app.teams.project_space.utils import counts


def project_task_items(records: ProjectSpaceRecords) -> list[dict[str, object]]:
    steps_by_task: dict[UUID, list[TaskStep]] = defaultdict(list)
    runs_by_task: dict[UUID, list[AgentRun]] = defaultdict(list)
    artifacts_by_task: dict[UUID, list[Artifact]] = defaultdict(list)
    messages_by_task: dict[UUID, list[TaskMessage]] = defaultdict(list)
    for step in records.steps:
        steps_by_task[step.task_id].append(step)
    for run in records.runs:
        if run.task_id is not None:
            runs_by_task[run.task_id].append(run)
    for artifact in records.artifacts:
        if artifact.task_id is not None:
            artifacts_by_task[artifact.task_id].append(artifact)
    for message in records.messages:
        messages_by_task[message.task_id].append(message)
    return [
        task_item(
            task,
            steps=steps_by_task.get(task.id, []),
            runs=runs_by_task.get(task.id, []),
            artifacts=artifacts_by_task.get(task.id, []),
            messages=messages_by_task.get(task.id, []),
        )
        for task in records.tasks
    ]


def task_item(
    task: Task,
    *,
    steps: list[TaskStep],
    runs: list[AgentRun],
    artifacts: list[Artifact],
    messages: list[TaskMessage],
) -> dict[str, object]:
    return {
        "task_id": task.id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "runtime_space_id": task.runtime_space_id,
        "step_count": len(steps),
        "step_status_counts": counts(step.status for step in steps),
        "active_run_count": sum(1 for run in runs if run.status in ACTIVE_RUN_STATUSES),
        "run_status_counts": counts(run.status for run in runs),
        "artifact_count": len(artifacts),
        "artifact_bytes": sum(max(artifact.size_bytes, 0) for artifact in artifacts),
        "message_count": len(messages),
        "updated_at": task.updated_at,
    }
