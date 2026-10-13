from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from temporalio import activity

from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.resource_queries import execution_resource_queries
from opsmesh.identity.authorization.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from opsmesh.orchestration.planning.attempts import TaskPlanningAttemptService
from opsmesh.orchestration.planning.revisions import RevisionRequestPlanner
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.workspaces.domain_items.models import RevisionRequest


class TaskPlanActivities:
    """Apply a persisted planning request and stage resulting Agent executions."""

    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.task_plan")
    def execute(self, request: ExecutionRequest) -> None:
        with self._services.transaction() as session:
            authorize_execution(
                session,
                request,
                action=WorkspaceAction.WRITE,
            )
            task = session.scalar(
                select(Task)
                .where(
                    Task.workspace_id == request.workspace_id,
                    Task.id == request.resource_id,
                )
                .with_for_update()
            )
            if task is None:
                return
            actor = ExecutionIdentityService(session).for_task(task.workspace_id, task.id)
            ResourceAuthorizationService(session, actor).require(
                task.workspace_id,
                ResourceKind.TASK,
                task.id,
                ResourceAction.UPDATE,
            )
            with execution_resource_queries(session, task.workspace_id, actor):
                revision_id = request.parameters.get("revision_request_id")
                if revision_id is not None and not isinstance(revision_id, str):
                    raise ValueError("Revision request reference must be a UUID string")
                revisions = list(
                    session.scalars(
                        select(RevisionRequest)
                        .where(
                            RevisionRequest.workspace_id == task.workspace_id,
                            RevisionRequest.task_id == task.id,
                            RevisionRequest.status == "queued",
                            *([RevisionRequest.id == UUID(revision_id)] if revision_id else []),
                        )
                        .order_by(RevisionRequest.created_at.asc(), RevisionRequest.id.asc())
                    )
                )
                planner = RevisionRequestPlanner(session)
                created = [
                    planner.create_step(task, revision)
                    for revision in revisions
                    if planner.existing_step(task, revision) is None
                ]
                for revision in revisions:
                    revision.status = "planned"
                    revision.resolved_at = datetime.now(UTC)
                    planner.append_task_message(
                        task,
                        message_type="revision.planned",
                        body="Revision request converted into follow-up work.",
                        payload={
                            "revision_request_id": str(revision.id),
                            "domain_item_id": str(revision.domain_item_id)
                            if revision.domain_item_id is not None
                            else None,
                            "assigned_agent_profile_id": str(revision.assigned_agent_profile_id)
                            if revision.assigned_agent_profile_id is not None
                            else None,
                        },
                    )
                if not revisions and task.agent_team_id is not None:
                    TaskPlanningAttemptService(session).ensure_initial_plan(
                        task,
                        transition_to_planning=task.status
                        in {"draft", "queued", "blocked", "failed"},
                    )
                if task.agent_team_id is not None and (created or task.project_plan is not None):
                    RunOrchestrationService(session).schedule_workspace_steps(
                        workspace_id=task.workspace_id,
                        requested_by_user_id=request.requested_by_user_id,
                    )
