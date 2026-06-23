from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.domains.models import RevisionRequest
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.tasks.models import Task
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.jobs import JobPayload
from backend.app.workers.revision_planner import RevisionRequestPlanner


class TaskPlanJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        task = self._task(job)
        if task is None:
            return

        revisions = self._queued_revisions(task)
        planner = RevisionRequestPlanner(self._context.session)
        created_steps = [
            planner.create_step(task, revision)
            for revision in revisions
            if planner.existing_step(task, revision) is None
        ]
        for revision in revisions:
            self._mark_revision_planned(planner, task, revision)

        if not revisions and task.agent_team_id is not None:
            TaskPlanningAttemptService(self._context.session).ensure_initial_plan(
                task,
                transition_to_planning=task.status in {"draft", "queued", "blocked", "failed"},
            )

        if task.agent_team_id is not None and (created_steps or task.project_plan is not None):
            RunOrchestrationService(
                self._context.session,
                queue=self._context.queue,
            ).schedule_workspace_steps(
                workspace_id=job.workspace_id,
                requested_by_user_id=job.requested_by_user_id,
            )
        self._context.session.commit()

    def _task(self, job: JobPayload) -> Task | None:
        return self._context.session.scalar(
            select(Task).where(
                Task.id == job.resource_id,
                Task.workspace_id == job.workspace_id,
            )
        )

    def _queued_revisions(self, task: Task) -> list[RevisionRequest]:
        return list(
            self._context.session.scalars(
                select(RevisionRequest)
                .where(
                    RevisionRequest.workspace_id == task.workspace_id,
                    RevisionRequest.task_id == task.id,
                    RevisionRequest.status == "queued",
                )
                .order_by(RevisionRequest.created_at.asc(), RevisionRequest.id.asc())
            )
        )

    def _mark_revision_planned(
        self,
        planner: RevisionRequestPlanner,
        task: Task,
        revision: RevisionRequest,
    ) -> None:
        planned_at = datetime.now(UTC)
        revision.status = "planned"
        revision.resolved_at = planned_at
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
