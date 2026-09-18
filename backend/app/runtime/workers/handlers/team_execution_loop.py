from backend.app.domains.access.execution import ExecutionIdentityService
from backend.app.domains.access.resource_queries import (
    ResourceQueryScope,
    bind_resource_queries,
    unbind_resource_queries,
)
from backend.app.domains.access.resources import (
    ResourceAction,
    ResourceAuthorizationService,
    ResourceKind,
)
from backend.app.domains.workspace.teams.execution.loop import TeamExecutionLoopService
from backend.app.runtime.environment.manager import DockerRuntimeManagerProvider
from backend.app.runtime.environment.service import RuntimeControlService
from backend.app.runtime.workers.contracts import JobPayload
from backend.app.runtime.workers.handlers.context import WorkerJobHandlerContext


class TeamExecutionLoopJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        session = self._context.session
        user = ExecutionIdentityService(session).restore(job.workspace_id, job.execution_identity)
        ResourceAuthorizationService(session, user).require(
            job.workspace_id, ResourceKind.TEAM, job.resource_id, ResourceAction.CONTROL
        )
        bind_resource_queries(
            session,
            ResourceQueryScope(
                job.workspace_id,
                user,
                mutation_action=ResourceAction.CONTROL,
            ),
        )
        try:
            self._run(job.model_copy(update={"requested_by_user_id": user.user_id}))
        finally:
            unbind_resource_queries(session)

    def _run(self, job: JobPayload) -> None:
        assert job.requested_by_user_id is not None
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
            manager_provider=DockerRuntimeManagerProvider(
                self._context.session,
                self._context.settings,
                self._context.docker_client(),
            ),
        )
