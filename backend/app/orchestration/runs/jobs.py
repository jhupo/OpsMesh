from backend.app.orchestration.runs.execution import RunExecutionDependencies, RunExecutionService
from backend.app.orchestration.runs.service import RunOrchestrationService
from backend.app.runtime.queues.context import WorkerJobHandlerContext
from backend.app.runtime.queues.contracts import JobPayload


class AgentRunJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        orchestration = RunOrchestrationService(self._context.session, self._context.queue)
        RunExecutionService(
            session=self._context.session,
            queue=self._context.queue,
            agent_runner=self._context.agent_runner,
            settings=self._context.settings,
            dependencies=RunExecutionDependencies(
                lifecycle=orchestration._run_lifecycle(),
                runtime_backends=self._context.runtime_backends,
            ),
            docker_client=self._context.runtime_docker_client,
        ).run_agent_sync(job)
