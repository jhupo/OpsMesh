from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.orchestration.runs.models import AgentRun, RunEvent
from backend.app.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.workspace.storage.artifact_models import Artifact

SUPPORTED_VIEW_TYPES = {"generic", "aigc", "novel", "research", "software"}


@dataclass(frozen=True)
class TaskObservationRecords:
    task: Task
    steps: list[TaskStep]
    runs: list[AgentRun]
    messages: list[TaskMessage]
    artifacts: list[Artifact]
    run_events: list[RunEvent]
    agents: dict[UUID, AgentProfile]
