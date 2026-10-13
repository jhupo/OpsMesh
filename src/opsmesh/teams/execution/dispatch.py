"""Stage Team advancement beside the product change that requires it."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.execution.reconciliation import require_reconciled_team
from opsmesh.teams.management.models import TEAM_RUNTIME_STATUS_KEY, AgentTeam
from opsmesh.teams.sessions.schemas import AgentTeamOrchestrationAdvanceRequest


def submit_team_orchestration(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
    user_id: UUID,
    configuration: AgentTeamOrchestrationAdvanceRequest,
    request_id: UUID | None = None,
    execution_identity: dict[str, object] | None = None,
    task_id: UUID | None = None,
) -> tuple[TeamOrchestrationRequest, str]:
    identities = ExecutionIdentityService(session)
    identity = execution_identity or identities.capture(workspace_id, user_id)
    user = identities.restore(workspace_id, identity)
    if user.user_id != user_id:
        raise ValueError("Team request identity differs from the initiating actor")
    require_team_transition_authority(
        session,
        workspace_id=workspace_id,
        team_id=team_id,
        task_id=task_id,
        user=user,
        configuration=configuration,
    )
    require_reconciled_team(session, workspace_id, team_id)
    return _persist_and_stage(
        session,
        workspace_id=workspace_id,
        team_id=team_id,
        user_id=user_id,
        configuration=configuration,
        request_id=request_id or uuid4(),
        identity=identity,
        task_id=task_id,
    )


def require_team_transition_authority(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
    task_id: UUID | None,
    user: AuthenticatedUser,
    configuration: AgentTeamOrchestrationAdvanceRequest,
) -> None:
    authorization = ResourceAuthorizationService(session, user)
    if task_id is None:
        authorization.require(workspace_id, ResourceKind.TEAM, team_id, ResourceAction.CONTROL)
        if (
            configuration.apply_command_center_actions
            and (not configuration.sources or "team_runtime" in configuration.sources)
            and (
                not configuration.actions
                or {"ensure_team_runtime", "start_team_runtime"}.intersection(configuration.actions)
            )
        ):
            WorkspaceAuthorizationService(session).require_workspace(
                user_id=user.user_id,
                workspace_id=workspace_id,
                action=WorkspaceAction.MANAGE_RUNTIME,
                authenticated_user=user,
            )
    else:
        task = session.scalar(
            select(Task).where(
                Task.workspace_id == workspace_id,
                Task.id == task_id,
                Task.agent_team_id == team_id,
            )
        )
        if task is None:
            raise ValueError("Team progress task is unavailable")
        authorization.require(workspace_id, ResourceKind.TASK, task_id, ResourceAction.INVOKE)


def _persist_and_stage(
    session: Session,
    *,
    workspace_id: UUID,
    team_id: UUID,
    user_id: UUID,
    configuration: AgentTeamOrchestrationAdvanceRequest,
    request_id: UUID,
    identity: dict[str, object],
    task_id: UUID | None,
) -> tuple[TeamOrchestrationRequest, str]:
    command_id = request_id
    command = session.scalar(
        select(TeamOrchestrationRequest).where(
            TeamOrchestrationRequest.workspace_id == workspace_id,
            TeamOrchestrationRequest.id == command_id,
        )
    )
    if command is None:
        if task_id is None:
            require_execution_admission(session)
        command = TeamOrchestrationRequest(
            id=command_id,
            workspace_id=workspace_id,
            team_id=team_id,
            task_id=task_id,
            execution_identity=identity,
            configuration=configuration.model_dump(mode="json"),
            status="queued",
        )
        session.add(command)
        session.flush([command])
    elif (
        command.team_id != team_id
        or command.task_id != task_id
        or command.execution_identity != identity
        or command.configuration != configuration.model_dump(mode="json")
    ):
        raise ValueError("Team request identity is already bound to different instructions")
    execution = ExecutionRequest(
        execution_id=command.id,
        workspace_id=workspace_id,
        resource_id=command.id,
        kind=ExecutionKind.TEAM_ORCHESTRATION,
        requested_by_user_id=user_id,
    )
    return command, stage_execution(session, execution)


def stage_team_task_progress(session: Session, *, task: Task, event_id: UUID) -> None:
    if task.agent_team_id is None or task.execution_identity is None:
        return
    team = session.scalar(
        select(AgentTeam).where(
            AgentTeam.workspace_id == task.workspace_id,
            AgentTeam.id == task.agent_team_id,
            AgentTeam.status == "active",
        )
    )
    if team is None:
        return
    state = team.default_task_policy.get(TEAM_RUNTIME_STATUS_KEY, {})
    if not isinstance(state, dict) or state.get("status") != "running":
        return
    # The event is a trusted product wake-up. Reauthorization belongs to its
    # Activity; a later revoked token must not roll back an already finished run.
    user_id = UUID(str(task.execution_identity["user_id"]))
    _persist_and_stage(
        session,
        workspace_id=task.workspace_id,
        team_id=team.id,
        user_id=user_id,
        request_id=event_id,
        identity=task.execution_identity,
        task_id=task.id,
        configuration=AgentTeamOrchestrationAdvanceRequest(
            dry_run=False,
            sources=["handoff_queue", "manager_queue"],
            actions=["schedule_downstream_steps"],
            reason="task_progress_event",
        ),
    )
