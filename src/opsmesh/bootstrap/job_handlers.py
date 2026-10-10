from sqlalchemy.orm import Session

from opsmesh.agents.providers.jobs import ModelProviderHealthJobHandler
from opsmesh.capabilities.mcp.managed_jobs import ManagedMcpJobHandler
from opsmesh.governance.audit.jobs import AuditIntegrityJobHandler
from opsmesh.governance.credentials.jobs import SecretReencryptJobHandler
from opsmesh.orchestration.conversations.jobs import ConversationAdvanceJobHandler
from opsmesh.orchestration.planning.jobs import TaskPlanJobHandler
from opsmesh.orchestration.webhooks.jobs import WebhookDeliveryJobHandler
from opsmesh.platform.settings.policy import operational_configuration
from opsmesh.resources.knowledge.jobs import KnowledgeIngestJobHandler
from opsmesh.resources.memory.embedding_jobs import MemoryEmbeddingJobHandler
from opsmesh.resources.memory.index_jobs import MemoryIndexJobHandler
from opsmesh.resources.transfers.jobs import WorkspaceArchiveExportJobHandler
from opsmesh.runtime.backends.factory import build_runtime_backend_registry
from opsmesh.runtime.instances.contracts import DockerRuntimeClient
from opsmesh.runtime.instances.jobs import RuntimeCleanupJobHandler, RuntimeControlJobHandler
from opsmesh.runtime.queues.context import WorkerJobHandlerContext
from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.runtime.workers.contracts import WorkerJobTypeHandler
from opsmesh.shared.config import Settings
from opsmesh.teams.execution.jobs import TeamExecutionLoopJobHandler


class WorkerJobHandler:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        settings: Settings | None = None,
        runtime_docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        context = WorkerJobHandlerContext(
            session=session,
            runtime_backends=build_runtime_backend_registry(
                runtime_docker_client,
                lambda: operational_configuration(session).file_transfer_timeout_seconds,
            ),
            queue=queue,
            settings=settings,
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
        JobType.AUDIT_INTEGRITY_CHECK: AuditIntegrityJobHandler(context),
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
