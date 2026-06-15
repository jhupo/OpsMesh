from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.teams.project_space.assemblers import build_project_space_response
from backend.app.teams.project_space.matching import (
    memory_entry_matches_project,
    workspace_file_matches_project,
)
from backend.app.teams.project_space.repository import TeamProjectSpaceRepository
from backend.app.teams.project_space.types import (
    ProjectSpaceRecords,
    relationship_ids,
    runtime_space_ids,
)


class TeamProjectSpaceService:
    """Project-space governance view for team-owned work and runtime resources."""

    def __init__(self, session: Session) -> None:
        self._repo = TeamProjectSpaceRepository(session)

    def get_project_space(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        limit: int = 200,
    ) -> dict[str, object] | None:
        team = self._repo.get_team(workspace_id, team_id)
        if team is None:
            return None

        members = self._repo.list_members(workspace_id, team_id)
        agents_by_id = self._repo.agents_by_id(
            workspace_id,
            [member.agent_profile_id for member in members],
        )
        tasks = self._repo.list_tasks(
            workspace_id,
            team_id,
            include_completed=include_completed,
            limit=limit,
        )
        task_ids = [task.id for task in tasks]
        steps = self._repo.list_steps(workspace_id, task_ids)
        runs = self._repo.list_runs(workspace_id, task_ids)
        artifacts = self._repo.list_artifacts(workspace_id, task_ids)
        messages = self._repo.list_messages(workspace_id, task_ids)

        space_ids = runtime_space_ids(team, tasks, steps, runs)
        project_relationships = relationship_ids(
            team_id=team_id,
            runtime_space_ids=space_ids,
            tasks=tasks,
            steps=steps,
            runs=runs,
            artifacts=artifacts,
        )
        files = [
            file
            for file in self._repo.list_active_files(workspace_id)
            if workspace_file_matches_project(file, project_relationships)
        ]
        memories = [
            entry
            for entry in self._repo.list_active_memory_entries(workspace_id)
            if memory_entry_matches_project(entry, project_relationships)
        ]

        records = ProjectSpaceRecords(
            team=team,
            members=members,
            agents_by_id=agents_by_id,
            tasks=tasks,
            steps=steps,
            runs=runs,
            artifacts=artifacts,
            messages=messages,
            runtime_spaces=self._repo.list_runtime_spaces(workspace_id, space_ids),
            quotas_by_space=self._repo.quotas_by_space(workspace_id, space_ids),
            reservations_by_space=self._repo.reservations_by_space(workspace_id, space_ids),
            events_by_space=self._repo.events_by_space(workspace_id, space_ids),
            files=files,
            memories=memories,
        )
        return build_project_space_response(
            workspace_id=workspace_id,
            team_id=team_id,
            generated_at=datetime.now(UTC),
            include_completed=include_completed,
            limit=limit,
            records=records,
        )
