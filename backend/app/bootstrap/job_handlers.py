from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeExecutor
from backend.app.agents.providers.jobs import ModelProviderHealthJobHandler
from backend.app.capabilities.mcp.execution.jobs import McpToolExecutionJobHandler
from backend.app.capabilities.mcp.managed_jobs import ManagedMcpJobHandler
from backend.app.capabilities.mcp.transport.contracts import McpToolAdapter, McpToolAdapterResolver
from backend.app.governance.audit.jobs import AuditIntegrityJobHandler
from backend.app.governance.credentials.jobs import SecretReencryptJobHandler
from backend.app.orchestration.conversations.jobs import ConversationAdvanceJobHandler
from backend.app.orchestration.planning.jobs import TaskPlanJobHandler
from backend.app.orchestration.runs.jobs import AgentRunJobHandler
from backend.app.orchestration.webhooks.jobs import WebhookDeliveryJobHandler
from backend.app.platform.settings.policy import operational_configuration
from backend.app.resources.knowledge.jobs import KnowledgeIngestJobHandler
from backend.app.resources.memory.embedding_jobs import MemoryEmbeddingJobHandler
from backend.app.resources.memory.index_jobs import MemoryIndexJobHandler
from backend.app.resources.transfers.jobs import WorkspaceArchiveExportJobHandler
from backend.app.runtime.backends.factory import build_runtime_backend_registry
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.instances.jobs import RuntimeCleanupJobHandler, RuntimeControlJobHandler
from backend.app.runtime.queues.context import WorkerJobHandlerContext
from backend.app.runtime.queues.contracts import JobPayload, JobType
from backend.app.runtime.queues.service import RedisQueue
from backend.app.runtime.workers.contracts import WorkerJobTypeHandler
from backend.app.shared.config import Settings
from backend.app.teams.execution.jobs import TeamExecutionLoopJobHandler


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
            runtime_backends=build_runtime_backend_registry(
                runtime_docker_client,
                lambda: operational_configuration(session).file_transfer_timeout_seconds,
            ),
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
        JobType.CONVERSATION_ADVANCE: ConversationAdvanceJobHandler(context),
        JobType.AGENT_RUN: AgentRunJobHandler(context),
        JobType.AUDIT_INTEGRITY_CHECK: AuditIntegrityJobHandler(context),
        JobType.MCP_TOOL_EXECUTION: McpToolExecutionJobHandler(context),
        JobType.MCP_PROCESS_CONTROL: ManagedMcpJobHandler(context),
        JobType.TASK_PLAN: TaskPlanJobHandler(context),
        JobType.TEAM_EXECUTION_LOOP: TeamExecutionLoopJobHandler(context),
        JobType.RUNTIME_CONTROL: RuntimeControlJobHandler(context),
        JobType.RUNTIME_CLEANUP: RuntimeCleanupJobHandler(context),
        JobType.WORKSPACE_ARCHIVE_EXPORT: WorkspaceArchiveExportJobHandler(context),
        JobType.MEMORY_INDEX: MemoryIndexJobHandler(context),
        JobType.MEMORY_EMBED: MemoryEmbeddingJobHandler(context),
        JobType.KNOWLEDGE_INGEST: KnowledgeIngestJobHandler(context),
        JobType.WEBHOOK_DELIVERY: WebhookDeliveryJobHandler(context),
        JobType.SECRET_REENCRYPT: SecretReencryptJobHandler(context),
        JobType.MODEL_PROVIDER_HEALTH_CHECK: ModelProviderHealthJobHandler(context),
    }
