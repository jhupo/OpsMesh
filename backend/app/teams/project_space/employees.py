from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from uuid import UUID

from backend.app.files.artifact_models import Artifact
from backend.app.teams.models import AgentTeamMember
from backend.app.teams.project_space.types import ProjectSpaceRecords
from backend.app.teams.project_space.utils import mark_latest


def employee_outputs(records: ProjectSpaceRecords) -> list[dict[str, object]]:
    return EmployeeOutputAssembler(records).build()


class EmployeeOutputAssembler:
    def __init__(self, records: ProjectSpaceRecords) -> None:
        self._records = records
        self._task_ids_by_agent: dict[UUID, set[UUID]] = defaultdict(set)
        self._step_counts_by_agent: dict[UUID, Counter[str]] = defaultdict(Counter)
        self._run_counts_by_agent: dict[UUID, Counter[str]] = defaultdict(Counter)
        self._artifacts_by_agent: dict[UUID, list[Artifact]] = defaultdict(list)
        self._memory_counts_by_agent: dict[UUID, int] = defaultdict(int)
        self._latest_activity_by_agent: dict[UUID, datetime] = {}

    def build(self) -> list[dict[str, object]]:
        self._index_steps()
        self._index_runs()
        self._index_artifacts()
        self._index_memories()
        return [self._member_payload(member) for member in self._records.members]

    def _index_steps(self) -> None:
        for step in self._records.steps:
            if step.assigned_agent_profile_id is None:
                continue
            agent_id = step.assigned_agent_profile_id
            self._task_ids_by_agent[agent_id].add(step.task_id)
            self._step_counts_by_agent[agent_id][step.status] += 1
            mark_latest(self._latest_activity_by_agent, agent_id, step.updated_at)

    def _index_runs(self) -> None:
        task_id_set = {task.id for task in self._records.tasks}
        for run in self._records.runs:
            if run.agent_profile_id is None:
                continue
            if run.task_id in task_id_set:
                self._task_ids_by_agent[run.agent_profile_id].add(run.task_id)
            self._run_counts_by_agent[run.agent_profile_id][run.status] += 1
            mark_latest(self._latest_activity_by_agent, run.agent_profile_id, run.updated_at)

    def _index_artifacts(self) -> None:
        for artifact in self._records.artifacts:
            if artifact.agent_profile_id is None:
                continue
            self._artifacts_by_agent[artifact.agent_profile_id].append(artifact)
            mark_latest(
                self._latest_activity_by_agent,
                artifact.agent_profile_id,
                artifact.created_at,
            )

    def _index_memories(self) -> None:
        for memory in self._records.memories:
            if memory.created_by_agent_profile_id is None:
                continue
            agent_id = memory.created_by_agent_profile_id
            self._memory_counts_by_agent[agent_id] += 1
            mark_latest(self._latest_activity_by_agent, agent_id, memory.updated_at)

    def _member_payload(self, member: AgentTeamMember) -> dict[str, object]:
        agent = self._records.agents_by_id.get(member.agent_profile_id)
        artifacts_for_agent = self._artifacts_by_agent.get(member.agent_profile_id, [])
        step_counts = self._step_counts_by_agent.get(member.agent_profile_id, Counter())
        run_counts = self._run_counts_by_agent.get(member.agent_profile_id, Counter())
        return {
            "team_member_id": member.id,
            "agent_profile_id": member.agent_profile_id,
            "agent_name": agent.name if agent is not None else None,
            "agent_role": agent.role if agent is not None else None,
            "team_role": member.team_role,
            "department": member.department,
            "position_title": member.position_title,
            "max_concurrent_tasks": member.max_concurrent_tasks,
            "active_task_count": self._active_task_count(member),
            "task_ids": sorted(
                self._task_ids_by_agent.get(member.agent_profile_id, set()),
                key=str,
            ),
            "step_status_counts": dict(sorted(step_counts.items())),
            "run_status_counts": dict(sorted(run_counts.items())),
            "artifact_count": len(artifacts_for_agent),
            "artifact_bytes": sum(
                max(artifact.size_bytes, 0) for artifact in artifacts_for_agent
            ),
            "memory_entry_count": self._memory_counts_by_agent.get(member.agent_profile_id, 0),
            "latest_activity_at": self._latest_activity_by_agent.get(member.agent_profile_id),
        }

    def _active_task_count(self, member: AgentTeamMember) -> int:
        task_id_set = {task.id for task in self._records.tasks}
        return len(
            {
                task_id
                for task_id in self._task_ids_by_agent.get(member.agent_profile_id, set())
                if task_id in task_id_set
            }
        )
