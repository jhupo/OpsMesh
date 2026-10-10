from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.resources.artifacts.models import Artifact

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
