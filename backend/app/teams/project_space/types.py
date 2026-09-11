from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.files.models import WorkspaceFile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember


@dataclass(frozen=True)
class ProjectSpaceRelationshipIds:
    team_id: UUID
    runtime_space_ids: set[UUID] = field(default_factory=set)
    task_ids: set[UUID] = field(default_factory=set)
    task_step_ids: set[UUID] = field(default_factory=set)
    agent_run_ids: set[UUID] = field(default_factory=set)
    artifact_ids: set[UUID] = field(default_factory=set)


@dataclass(frozen=True)
class ProjectSpaceRecords:
    team: AgentTeam
    members: list[AgentTeamMember]
    agents_by_id: dict[UUID, AgentProfile]
    tasks: list[Task]
    steps: list[TaskStep]
    runs: list[AgentRun]
    artifacts: list[Artifact]
    messages: list[TaskMessage]
    runtime_spaces: list[RuntimeSpace]
    quotas_by_space: dict[UUID, list[RuntimeSpaceQuota]]
    reservations_by_space: dict[UUID, list[RuntimeSpaceReservation]]
    events_by_space: dict[UUID, list[RuntimeSpaceEvent]]
    files: list[WorkspaceFile]
    memories: list[WorkspaceMemoryEntry]


def runtime_space_ids(
    team: AgentTeam,
    tasks: list[Task],
    steps: list[TaskStep],
    runs: list[AgentRun],
) -> set[UUID]:
    ids: set[UUID] = set()
    if team.runtime_space_id is not None:
        ids.add(team.runtime_space_id)
    ids.update(task.runtime_space_id for task in tasks if task.runtime_space_id is not None)
    ids.update(step.runtime_space_id for step in steps if step.runtime_space_id is not None)
    ids.update(run.runtime_space_id for run in runs if run.runtime_space_id is not None)
    return ids


def relationship_ids(
    *,
    team_id: UUID,
    runtime_space_ids: set[UUID],
    tasks: list[Task],
    steps: list[TaskStep],
    runs: list[AgentRun],
    artifacts: list[Artifact],
) -> ProjectSpaceRelationshipIds:
    return ProjectSpaceRelationshipIds(
        team_id=team_id,
        runtime_space_ids=runtime_space_ids,
        task_ids={task.id for task in tasks},
        task_step_ids={step.id for step in steps},
        agent_run_ids={run.id for run in runs},
        artifact_ids={artifact.id for artifact in artifacts},
    )
