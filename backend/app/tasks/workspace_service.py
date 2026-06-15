from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.audit.service import AuditService
from backend.app.db.pagination import page_scalars
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.queue import RedisQueue


@dataclass(frozen=True, slots=True)
class TaskCreateCommand:
    title: str
    agent_team_id: UUID | None = None
    runtime_space_id: UUID | None = None
    domain_type: str = "general"
    description: str = ""
    priority: int = 0
    input: dict[str, object] = field(default_factory=dict)
    generic_state: dict[str, object] = field(default_factory=dict)
    domain_state: dict[str, object] = field(default_factory=dict)


class WorkspaceTaskService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_tasks(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[Task], int]:
        statement = select(Task).where(Task.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Task.status == status)
        return page_scalars(self._session, statement.order_by(Task.created_at.desc()), page)

    def create_task(
        self,
        workspace_id: UUID,
        created_by_user_id: UUID,
        command: TaskCreateCommand,
        *,
        queue: RedisQueue | None = None,
    ) -> Task:
        payload = _task_payload(command)
        runtime_spaces = RuntimeSpaceService(self._session)
        team = self._team_for_task(workspace_id, command)

        if command.runtime_space_id is not None:
            runtime_spaces.require_runtime_space_for_target(
                workspace_id=workspace_id,
                runtime_space_id=command.runtime_space_id,
                target_type="agent_team" if team is not None else "workspace",
                target_id=team.id if team is not None else workspace_id,
            )

        if team is not None:
            payload = self._team_task_payload(
                workspace_id=workspace_id,
                team=team,
                payload=payload,
                runtime_spaces=runtime_spaces,
            )

        task = Task(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            **payload,
        )
        self._session.add(task)
        self._session.flush()

        if task.agent_team_id is not None:
            TaskPlanningAttemptService(self._session).ensure_initial_plan(task)

        initial_run = None
        initial_run_enqueued = False
        if task.project_plan is not None or task.agent_team_id is None:
            orchestration = RunOrchestrationService(self._session, queue=queue)
            initial_run = orchestration.create_queued_run_for_task(task)
            if initial_run is not None and queue is not None:
                initial_run_enqueued = orchestration.enqueue_run(
                    initial_run,
                    created_by_user_id,
                )

        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=created_by_user_id,
            action="task.created",
            target_type="task",
            target_id=task.id,
            metadata={
                "title": task.title,
                "domain_type": task.domain_type,
                "initial_run_id": str(initial_run.id) if initial_run is not None else None,
                "initial_run_enqueued": initial_run_enqueued,
            },
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def get_task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )

    def _team_for_task(
        self,
        workspace_id: UUID,
        command: TaskCreateCommand,
    ) -> AgentTeam | None:
        if command.agent_team_id is None:
            return None
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == command.agent_team_id,
            )
        )
        if team is None:
            raise ValueError("Team not found")
        return team

    def _team_task_payload(
        self,
        *,
        workspace_id: UUID,
        team: AgentTeam,
        payload: dict[str, object],
        runtime_spaces: RuntimeSpaceService,
    ) -> dict[str, object]:
        payload = dict(payload)
        if payload.get("runtime_space_id") is None:
            payload["runtime_space_id"] = team.runtime_space_id
        runtime_space_id = _uuid_or_none(payload.get("runtime_space_id"))
        if runtime_space_id is not None:
            runtime_spaces.require_runtime_space_for_target(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                target_type="agent_team",
                target_id=team.id,
            )
        payload["team_snapshot"] = build_team_snapshot(
            self._session,
            workspace_id=workspace_id,
            team_id=team.id,
        )
        return payload


def _task_payload(command: TaskCreateCommand) -> dict[str, object]:
    return {
        "agent_team_id": command.agent_team_id,
        "runtime_space_id": command.runtime_space_id,
        "domain_type": command.domain_type,
        "title": command.title,
        "description": command.description,
        "priority": command.priority,
        "input": command.input,
        "generic_state": command.generic_state,
        "domain_state": command.domain_state,
    }


def _uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
