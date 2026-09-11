from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep

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
