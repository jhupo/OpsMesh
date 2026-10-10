"""Workspace-scoped task creation, admission, and retrieval."""

from contextlib import nullcontext
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resource_queries import (
    execution_resource_queries,
    resource_query_scope,
)
from opsmesh.identity.authorization.resources import (
    ResourceAccessDenied,
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.orchestration.definitions.application import (
    OrchestrationDefinitionApplicationService,
)
from opsmesh.orchestration.planning.attempts import TaskPlanningAttemptService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.runtime.spaces.service import RuntimeSpaceService
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.pagination import PageParams
from opsmesh.teams.management.models import AgentTeam
from opsmesh.teams.sessions.snapshots import build_team_snapshot
from opsmesh.workspaces.projects.models import WorkspaceProject


@dataclass(frozen=True)
class TaskCreateCommand:
    title: str
    agent_profile_id: UUID | None = None
    agent_team_id: UUID | None = None
    runtime_space_id: UUID | None = None
    workspace_project_id: UUID | None = None
    domain_type: str = "general"
    description: str = ""
    priority: int = 0
    input: dict[str, object] = field(default_factory=dict)
    generic_state: dict[str, object] = field(default_factory=dict)
    domain_state: dict[str, object] = field(default_factory=dict)
    orchestration_definition_id: UUID | None = None
    orchestration_version: int | None = None


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
        task, _ = self._create_task(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            created_by_agent_run_id=None,
            command=command,
            queue=queue,
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def create_automation_task(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        command: TaskCreateCommand,
        execution_identity: dict[str, object],
    ) -> Task:
        """Persist admission in the caller's inbox transaction; queue recovery dispatches it."""
        task, _ = self._create_task(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            created_by_agent_run_id=None,
            command=command,
            queue=None,
            initiating_identity=execution_identity,
        )
        return task

    def create_conversation_task(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        command: TaskCreateCommand,
        execution_identity: dict[str, object],
        parent_run_id: UUID | None = None,
    ) -> Task:
        """Admission inside the conversation transaction; queue recovery dispatches after commit."""
        task, _ = self._create_task(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            created_by_agent_run_id=parent_run_id,
            command=command,
            queue=None,
            initiating_identity=execution_identity,
        )
        return task

    def create_subworkflow_task(
        self,
        *,
        parent_task: Task,
        parent_run_id: UUID,
        title: str,
        description: str,
        input_payload: dict[str, object],
        orchestration_definition_id: UUID,
        orchestration_version: int,
        queue: RedisQueue | None = None,
    ) -> tuple[Task, AgentRun | None]:
        """Materialize a child task through the same task admission path as user tasks."""
        if parent_task.agent_team_id is None:
            raise ValueError("Subworkflow execution requires a parent task team")
        command = TaskCreateCommand(
            title=title,
            agent_team_id=parent_task.agent_team_id,
            runtime_space_id=parent_task.runtime_space_id,
            workspace_project_id=parent_task.workspace_project_id,
            domain_type=parent_task.domain_type,
            description=description,
            priority=parent_task.priority,
            input=input_payload,
            generic_state={
                "subworkflow_parent_task_id": str(parent_task.id),
                "subworkflow_parent_run_id": str(parent_run_id),
            },
            domain_state={},
            orchestration_definition_id=orchestration_definition_id,
            orchestration_version=orchestration_version,
        )
        return self._create_task(
            workspace_id=parent_task.workspace_id,
            created_by_user_id=None,
            created_by_agent_run_id=parent_run_id,
            command=command,
            queue=queue,
        )

    def _create_task(
        self,
        *,
        workspace_id: UUID,
        created_by_user_id: UUID | None,
        created_by_agent_run_id: UUID | None,
        command: TaskCreateCommand,
        queue: RedisQueue | None,
        initiating_identity: dict[str, object] | None = None,
    ) -> tuple[Task, AgentRun | None]:
        identities = ExecutionIdentityService(self._session)
        identity: dict[str, object] | None
        if initiating_identity is not None:
            identity = initiating_identity
            if str(created_by_user_id) != identity.get("user_id"):
                raise ResourceAccessDenied()
        elif created_by_user_id is not None:
            identity = identities.capture(workspace_id, created_by_user_id)
        elif created_by_agent_run_id is not None:
            parent = self._session.scalar(
                select(Task)
                .join(
                    AgentRun,
                    AgentRun.task_id == Task.id,
                )
                .where(
                    Task.workspace_id == workspace_id,
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.id == created_by_agent_run_id,
                )
            )
            if parent is None:
                raise ResourceAccessDenied()
            identity = parent.execution_identity
        else:
            raise ResourceAccessDenied()
        user = identities.restore(workspace_id, identity)
        access = ResourceAuthorizationService(self._session, user)
        for kind, identifier, action in (
            (ResourceKind.AGENT, command.agent_profile_id, ResourceAction.INVOKE),
            (ResourceKind.TEAM, command.agent_team_id, ResourceAction.INVOKE),
            (ResourceKind.PROJECT, command.workspace_project_id, ResourceAction.INVOKE),
            (ResourceKind.WORKFLOW, command.orchestration_definition_id, ResourceAction.INVOKE),
            (ResourceKind.RUNTIME_SPACE, command.runtime_space_id, ResourceAction.INVOKE),
        ):
            if identifier is not None:
                access.require(workspace_id, kind, identifier, action)
        scope = resource_query_scope(self._session)
        with (
            execution_resource_queries(self._session, workspace_id, user)
            if scope is None
            else nullcontext()
        ):
            return self._materialize_task(
                workspace_id=workspace_id,
                created_by_user_id=created_by_user_id,
                created_by_agent_run_id=created_by_agent_run_id,
                command=command,
                queue=queue,
                identity=identity,
            )

    def _materialize_task(
        self,
        *,
        workspace_id: UUID,
        created_by_user_id: UUID | None,
        created_by_agent_run_id: UUID | None,
        command: TaskCreateCommand,
        queue: RedisQueue | None,
        identity: dict[str, object] | None,
    ) -> tuple[Task, AgentRun | None]:
        payload = _task_payload(command)
        if command.agent_profile_id is not None:
            if command.agent_team_id is not None:
                raise ValueError("A task cannot bind both an individual agent and a team")
            profile = self._session.scalar(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == workspace_id,
                    AgentProfile.id == command.agent_profile_id,
                    AgentProfile.status == "active",
                    AgentProfile.archived_at.is_(None),
                )
            )
            if profile is None:
                raise ValueError("Agent not found or unavailable")
            payload["owner_agent_profile_id"] = profile.id
        runtime_spaces = RuntimeSpaceService(self._session)
        team = self._team_for_task(workspace_id, command)
        self._validate_project(workspace_id, command.workspace_project_id)

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

        resolved_runtime = _uuid_or_none(payload.get("runtime_space_id"))
        if resolved_runtime is not None:
            user = ExecutionIdentityService(self._session).restore(workspace_id, identity)
            ResourceAuthorizationService(self._session, user).require(
                workspace_id, ResourceKind.RUNTIME_SPACE, resolved_runtime, ResourceAction.INVOKE
            )

        task = Task(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            created_by_agent_run_id=created_by_agent_run_id,
            execution_identity=identity,
            **payload,
        )
        self._session.add(task)
        self._session.flush()

        if task.orchestration_definition_id is not None:
            OrchestrationDefinitionApplicationService(self._session).apply_to_task(
                task,
                task.orchestration_definition_id,
                task.orchestration_version,
                created_by_user_id,
            )
        elif task.agent_team_id is not None:
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

        metadata: dict[str, object] = {
            "title": task.title,
            "domain_type": task.domain_type,
            "initial_run_id": str(initial_run.id) if initial_run is not None else None,
            "initial_run_enqueued": initial_run_enqueued,
            "workspace_project_id": str(task.workspace_project_id)
            if task.workspace_project_id is not None
            else None,
            "created_by_agent_run_id": str(created_by_agent_run_id)
            if created_by_agent_run_id is not None
            else None,
        }
        audit = AuditService(self._session)
        if created_by_user_id is None:
            audit.record_system_action(
                workspace_id=workspace_id,
                action="task.created",
                target_type="task",
                target_id=task.id,
                metadata=metadata,
            )
        else:
            audit.record_user_action(
                workspace_id=workspace_id,
                user_id=created_by_user_id,
                action="task.created",
                target_type="task",
                target_id=task.id,
                metadata=metadata,
            )
        self._session.flush()
        return task, initial_run

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
                AgentTeam.status == "active",
            )
        )
        if team is None:
            raise ValueError("Team not found")
        return team

    def _validate_project(self, workspace_id: UUID, project_id: UUID | None) -> None:
        if project_id is None:
            return
        project = self._session.scalar(
            select(WorkspaceProject.id).where(
                WorkspaceProject.workspace_id == workspace_id,
                WorkspaceProject.id == project_id,
                WorkspaceProject.status == "active",
            )
        )
        if project is None:
            raise ValueError("Workspace project not found")

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
        if team.manager_agent_profile_id is not None:
            payload["owner_agent_profile_id"] = team.manager_agent_profile_id
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
        "workspace_project_id": command.workspace_project_id,
        "domain_type": command.domain_type,
        "title": command.title,
        "description": command.description,
        "priority": command.priority,
        "input": command.input,
        "generic_state": command.generic_state,
        "domain_state": command.domain_state,
        "orchestration_definition_id": command.orchestration_definition_id,
        "orchestration_version": command.orchestration_version,
    }


def _uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
