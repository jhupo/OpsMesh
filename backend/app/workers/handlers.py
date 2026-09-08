from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeExecutor
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.core.config import Settings
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.workers.job_handlers import (
    AgentRunJobHandler,
    AuditIntegrityJobHandler,
    McpToolExecutionJobHandler,
    MemoryIndexJobHandler,
    ModelProviderHealthJobHandler,
    RuntimeCleanupJobHandler,
    RuntimeControlJobHandler,
    SecretReencryptJobHandler,
    TaskPlanJobHandler,
    TeamExecutionLoopJobHandler,
    WebhookDeliveryJobHandler,
    WorkspaceArchiveExportJobHandler,
)
from backend.app.workers.job_handlers.base import WorkerJobTypeHandler
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue


class WorkerJobHandler:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        agent_runner: AgentRuntimeExecutor | None = None,
        settings: Settings | None = None,
        mcp_adapter: McpToolAdapter | McpToolAdapterResolver | None = None,
        runtime_docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        context = WorkerJobHandlerContext(
            session=session,
            queue=queue,
            agent_runner=agent_runner,
            settings=settings,
            mcp_adapter=mcp_adapter,
            runtime_docker_client=runtime_docker_client,
        )
        self._handlers = _build_handler_registry(context)

    def handle(self, job: JobPayload) -> None:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            raise ValueError(f"Unsupported job type: {job.job_type}")
        handler.handle(job)


def _build_handler_registry(
    context: WorkerJobHandlerContext,
) -> dict[JobType, WorkerJobTypeHandler]:
    return {
        JobType.AGENT_RUN: AgentRunJobHandler(context),
        JobType.AUDIT_INTEGRITY_CHECK: AuditIntegrityJobHandler(context),
        JobType.MCP_TOOL_EXECUTION: McpToolExecutionJobHandler(context),
        JobType.TASK_PLAN: TaskPlanJobHandler(context),
        JobType.TEAM_EXECUTION_LOOP: TeamExecutionLoopJobHandler(context),
        JobType.RUNTIME_CONTROL: RuntimeControlJobHandler(context),
        JobType.RUNTIME_CLEANUP: RuntimeCleanupJobHandler(context),
        JobType.WORKSPACE_ARCHIVE_EXPORT: WorkspaceArchiveExportJobHandler(context),
        JobType.MEMORY_INDEX: MemoryIndexJobHandler(context),
        JobType.WEBHOOK_DELIVERY: WebhookDeliveryJobHandler(context),
        JobType.SECRET_REENCRYPT: SecretReencryptJobHandler(context),
        JobType.MODEL_PROVIDER_HEALTH_CHECK: ModelProviderHealthJobHandler(context),
    }
