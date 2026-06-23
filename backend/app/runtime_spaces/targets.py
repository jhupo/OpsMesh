from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceBinding
from backend.app.runtimes.models import RuntimeTemplate
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam

TARGET_TYPES_BY_SCOPE = {
    "workspace": "workspace",
    "team": "agent_team",
    "task": "task",
}


class RuntimeSpaceTargetService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def normalize_target_id(
        self,
        *,
        workspace_id: UUID,
        scope: str,
        target_id: UUID | None,
    ) -> UUID:
        if scope == "workspace":
            return workspace_id
        if target_id is None:
            raise ValueError("target_id is required for team and task runtime spaces")
        if scope == "team":
            if not self._active_team_exists(workspace_id, target_id):
                raise ValueError("Team not found")
            return target_id
        if scope == "task":
            if not self._task_exists(workspace_id, target_id):
                raise ValueError("Task not found")
            return target_id
        raise ValueError("Invalid runtime space scope")

    def require_runtime_template(self, template_id: UUID) -> None:
        template = self._session.scalar(
            select(RuntimeTemplate.id).where(
                RuntimeTemplate.id == template_id,
                RuntimeTemplate.status == "active",
            )
        )
        if template is None:
            raise ValueError("Runtime template not found")

    def bind_target(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        target_id: UUID,
    ) -> None:
        target_type = TARGET_TYPES_BY_SCOPE[runtime_space.scope]
        existing = self._session.scalar(
            select(RuntimeSpaceBinding).where(
                RuntimeSpaceBinding.workspace_id == workspace_id,
                RuntimeSpaceBinding.target_type == target_type,
                RuntimeSpaceBinding.target_id == target_id,
                RuntimeSpaceBinding.status == "active",
            )
        )
        if existing is not None:
            raise ValueError("Target already has an active runtime space")
        self._session.add(
            RuntimeSpaceBinding(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space.id,
                target_type=target_type,
                target_id=target_id,
            )
        )

    def require_runtime_space_for_target(
        self,
        runtime_space: RuntimeSpace,
        *,
        workspace_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> RuntimeSpace:
        if runtime_space.scope == "workspace":
            return runtime_space

        expected_target_type = TARGET_TYPES_BY_SCOPE.get(runtime_space.scope)
        if expected_target_type != target_type:
            raise ValueError("Runtime space is not available for this target")
        binding = self._session.scalar(
            select(RuntimeSpaceBinding.id).where(
                RuntimeSpaceBinding.workspace_id == workspace_id,
                RuntimeSpaceBinding.runtime_space_id == runtime_space.id,
                RuntimeSpaceBinding.target_type == target_type,
                RuntimeSpaceBinding.target_id == target_id,
                RuntimeSpaceBinding.status == "active",
            )
        )
        if binding is None:
            raise ValueError("Runtime space is not available for this target")
        return runtime_space

    def _active_team_exists(self, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == team_id,
                    AgentTeam.status == "active",
                )
            )
            is not None
        )

    def _task_exists(self, workspace_id: UUID, task_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(Task.id).where(Task.workspace_id == workspace_id, Task.id == task_id)
            )
            is not None
        )
