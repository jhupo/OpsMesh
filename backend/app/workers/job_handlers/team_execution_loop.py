from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.teams.execution_loop import TeamExecutionLoopService
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.jobs import JobPayload


class TeamExecutionLoopJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        if job.requested_by_user_id is None:
            raise ValueError("Team execution loop jobs require requested_by_user_id")
        TeamExecutionLoopService(self._context.session).run_iteration(
            workspace_id=job.workspace_id,
            team_id=job.resource_id,
            actor_user_id=job.requested_by_user_id,
            dry_run=False,
            apply_command_center_actions=True,
            enqueue_runs=True,
            finalize_ready_tasks=True,
            queue=self._context.queue,
            runtime_control=self._runtime_control(),
            reason="worker_team_execution_loop",
            metadata={
                "source": "worker",
                "job_id": str(job.job_id),
                "routing": dict(job.routing),
            },
        )

    def _runtime_control(self) -> RuntimeControlService | None:
        if self._context.settings is None:
            return None
        return RuntimeControlService(
            self._context.session,
            settings=self._context.settings,
            docker_client=self._context.docker_client(),
        )
