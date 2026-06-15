import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.api.services.exports import WorkspaceExportService
from backend.app.capabilities.execution import McpToolExecutionService
from backend.app.capabilities.mcp_adapter_resolver import McpAdapterResolver
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.capabilities.mcp_execution_types import McpExecutionRequest
from backend.app.core.config import Settings
from backend.app.domains.models import RevisionRequest
from backend.app.files.storage import create_storage
from backend.app.memory.indexing import WorkspaceMemoryIndexingService
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.orchestration.run_execution import (
    RunExecutionDependencies,
    RunExecutionService,
)
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.dependencies import get_docker_runtime_client
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.secrets.rotation import HostedSecretReencryptService
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.execution_loop import TeamExecutionLoopService
from backend.app.webhooks.service import WebhookDeliveryService
from backend.app.workers.job_routing import (
    optional_uuid,
    positive_float,
    required_string,
)
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workers.runtime_control_handler import (
    RuntimeCleanupJobHandler,
    RuntimeControlJobHandler,
)


class WorkerJobHandler:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        agent_runner: AgentRunner | None = None,
        settings: Settings | None = None,
        mcp_adapter: McpToolAdapter | McpToolAdapterResolver | None = None,
        runtime_docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._agent_runner = agent_runner
        self._settings = settings
        self._mcp_adapter = mcp_adapter
        self._runtime_docker_client = runtime_docker_client

    def handle(self, job: JobPayload) -> None:
        match job.job_type:
            case JobType.AGENT_RUN:
                orchestration = RunOrchestrationService(self._session, self._queue)
                RunExecutionService(
                    session=self._session,
                    queue=self._queue,
                    agent_runner=self._agent_runner,
                    settings=self._settings,
                    dependencies=RunExecutionDependencies(
                        lifecycle=orchestration._run_lifecycle(),
                    ),
                ).run_agent_sync(job)
            case JobType.MCP_TOOL_EXECUTION:
                self._handle_mcp_tool_execution(job)
            case JobType.TASK_PLAN:
                self._handle_task_plan(job)
            case JobType.TEAM_EXECUTION_LOOP:
                self._handle_team_execution_loop(job)
            case JobType.RUNTIME_CONTROL:
                self._handle_runtime_control(job)
            case JobType.RUNTIME_CLEANUP:
                self._handle_runtime_cleanup(job)
            case JobType.WORKSPACE_ARCHIVE_EXPORT:
                if self._settings is None:
                    error = "Worker settings are required for workspace archive export"
                    WorkspaceExportService(self._session).fail_archive_export_job(
                        job=job,
                        error=error,
                    )
                    raise ValueError(error)
                WorkspaceExportService(self._session).run_archive_export_job(
                    job=job,
                    storage=create_storage(self._settings),
                )
            case JobType.MEMORY_INDEX:
                self._handle_memory_index(job)
            case JobType.WEBHOOK_DELIVERY:
                if self._settings is None:
                    raise ValueError("Worker settings are required for webhook delivery")
                WebhookDeliveryService(
                    self._session,
                    self._secret_service(),
                ).deliver(
                    workspace_id=job.workspace_id,
                    delivery_attempt_id=job.resource_id,
                )
            case JobType.SECRET_REENCRYPT:
                self._handle_secret_reencrypt(job)
            case JobType.MODEL_PROVIDER_HEALTH_CHECK:
                self._handle_model_provider_health_check(job)
            case _:
                raise ValueError(f"Unsupported job type: {job.job_type}")

    def _secret_service(self) -> SecretEncryptionService:
        if self._settings is None:
            raise ValueError("Worker settings are required for secret decryption")
        return SecretEncryptionService(
            secret=self._settings.credential_encryption_secret,
            key_id=self._settings.credential_encryption_key_id,
            previous_secrets=self._settings.credential_encryption_previous_secrets,
        )

    def _handle_task_plan(self, job: JobPayload) -> None:
        task = self._session.scalar(
            select(Task).where(
                Task.workspace_id == job.workspace_id,
                Task.id == job.resource_id,
            )
        )
        if task is None:
            return

        revisions = self._session.scalars(
            select(RevisionRequest)
            .where(
                RevisionRequest.workspace_id == job.workspace_id,
                RevisionRequest.task_id == task.id,
                RevisionRequest.status == "queued",
            )
            .order_by(RevisionRequest.created_at.asc(), RevisionRequest.id.asc())
        ).all()
        created_steps = [
            self._create_revision_step(task, revision)
            for revision in revisions
            if self._revision_step(task, revision) is None
        ]
        now = datetime.now(UTC)
        for revision in revisions:
            revision.status = "planned"
            revision.resolved_at = now
            self._append_task_message(
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
            TaskPlanningAttemptService(self._session).ensure_initial_plan(
                task,
                transition_to_planning=task.status in {"draft", "queued", "blocked", "failed"},
            )

        if task.agent_team_id is not None and (created_steps or task.project_plan is not None):
            RunOrchestrationService(
                self._session,
                queue=self._queue,
            ).schedule_workspace_steps(
                workspace_id=job.workspace_id,
                requested_by_user_id=job.requested_by_user_id,
            )
        self._session.commit()

    def _handle_team_execution_loop(self, job: JobPayload) -> None:
        if job.requested_by_user_id is None:
            raise ValueError("Team execution loop jobs require requested_by_user_id")
        TeamExecutionLoopService(self._session).run_iteration(
            workspace_id=job.workspace_id,
            team_id=job.resource_id,
            actor_user_id=job.requested_by_user_id,
            dry_run=False,
            apply_command_center_actions=True,
            enqueue_runs=True,
            finalize_ready_tasks=True,
            queue=self._queue,
            runtime_control=(
                RuntimeControlService(
                    self._session,
                    settings=self._settings,
                    docker_client=self._runtime_docker_client or get_docker_runtime_client(),
                )
                if self._settings is not None
                else None
            ),
            reason="worker_team_execution_loop",
            metadata={
                "source": "worker",
                "job_id": str(job.job_id),
                "routing": dict(job.routing),
            },
        )

    def _handle_runtime_cleanup(self, job: JobPayload) -> None:
        RuntimeCleanupJobHandler(self._session).handle(job)

    def _handle_runtime_control(self, job: JobPayload) -> None:
        RuntimeControlJobHandler(
            self._session,
            settings=self._settings,
            docker_client=self._runtime_docker_client or get_docker_runtime_client(),
        ).handle(job)

    def _handle_memory_index(self, job: JobPayload) -> None:
        source_type = required_string(
            job.routing,
            "source_type",
            context="MCP tool execution job",
        )
        service = WorkspaceMemoryIndexingService(self._session)
        match source_type:
            case "task":
                service.refresh_task(workspace_id=job.workspace_id, task_id=job.resource_id)
            case "workspace_file":
                service.refresh_file(workspace_id=job.workspace_id, file_id=job.resource_id)
            case "artifact":
                service.refresh_artifact(workspace_id=job.workspace_id, artifact_id=job.resource_id)
            case _:
                raise ValueError(f"Unsupported memory index source_type: {source_type}")
        self._session.commit()

    def _handle_secret_reencrypt(self, job: JobPayload) -> None:
        scope = job.routing.get("scope")
        workspace_id = None if scope == "global" else job.workspace_id
        HostedSecretReencryptService(
            self._session,
            self._secret_service(),
        ).reencrypt(workspace_id=workspace_id)
        self._session.commit()

    def _handle_model_provider_health_check(self, job: JobPayload) -> None:
        if job.requested_by_user_id is None:
            raise ValueError("Model provider health check jobs require requested_by_user_id")
        asyncio.run(
            ModelProviderCredentialService(
                self._session,
                self._secret_service(),
            ).run_health_check(
                workspace_id=job.workspace_id,
                credential_id=job.resource_id,
                actor_user_id=job.requested_by_user_id,
                probes=_provider_health_probes(job.routing.get("probes")),
                timeout_seconds=positive_float(
                    job.routing.get("timeout_seconds"),
                    default=15,
                    key="timeout_seconds",
                    context="Model provider health check job",
                    maximum=60,
                ),
            )
        )

    def _handle_mcp_tool_execution(self, job: JobPayload) -> None:
        payload = job.routing
        tool_name = required_string(payload, "tool_name", context="MCP tool execution job")
        arguments = _dict(payload.get("arguments"))
        runtime_allowed_tools = _string_tuple(payload.get("runtime_allowed_tools"))
        server_id = optional_uuid(
            payload.get("mcp_server_id"),
            context="MCP tool execution",
        )
        agent_run_id = (
            optional_uuid(payload.get("agent_run_id"), context="MCP tool execution")
            or job.resource_id
        )

        McpToolExecutionService(
            self._session,
            self._mcp_adapter or McpAdapterResolver(),
            settings=self._settings,
        ).execute(
            McpExecutionRequest(
                workspace_id=job.workspace_id,
                agent_run_id=agent_run_id,
                mcp_server_id=server_id,
                tool_name=tool_name,
                arguments=arguments,
                runtime_allowed_tools=runtime_allowed_tools,
            )
        )
        self._session.commit()

    def _create_revision_step(self, task: Task, revision: RevisionRequest) -> TaskStep:
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            assigned_agent_profile_id=revision.assigned_agent_profile_id,
            runtime_space_id=task.runtime_space_id,
            work_package_id=_revision_work_package_id(revision),
            title="Revision request",
            description=revision.instruction,
            status="queued",
            order_index=self._next_step_order(task),
            acceptance_criteria=[revision.instruction],
            review_policy={"reviewer": "manager", "mode": "revision_request_review"},
            dependencies={
                "revision_request": {
                    "id": str(revision.id),
                    "domain_item_id": str(revision.domain_item_id)
                    if revision.domain_item_id is not None
                    else None,
                    "payload": revision.payload,
                }
            },
        )
        self._session.add(step)
        self._session.flush([step])
        return step

    def _revision_step(self, task: Task, revision: RevisionRequest) -> TaskStep | None:
        return self._session.scalar(
            select(TaskStep).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
                TaskStep.work_package_id == _revision_work_package_id(revision),
            )
        )

    def _next_step_order(self, task: Task) -> int:
        current = self._session.scalar(
            select(func.coalesce(func.max(TaskStep.order_index), 0)).where(
                TaskStep.workspace_id == task.workspace_id,
                TaskStep.task_id == task.id,
            )
        )
        return int(current or 0) + 1

    def _append_task_message(
        self,
        task: Task,
        *,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type=message_type,
            body=body,
            payload=payload,
        )


def _dict(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("MCP tool execution job arguments must be an object")
    return dict(value)


def _provider_health_probes(value: object) -> tuple[str, ...]:
    if value is None:
        return ("models", "inference")
    if not isinstance(value, list | tuple):
        raise ValueError("probes must be a list")
    probes = tuple(dict.fromkeys(item for item in value if isinstance(item, str) and item))
    if not probes:
        raise ValueError("probes must include at least one probe")
    invalid = sorted(set(probes) - {"models", "inference"})
    if invalid:
        raise ValueError(f"Unsupported provider health probe: {', '.join(invalid)}")
    return probes


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("MCP tool execution runtime_allowed_tools must be a list")
    return tuple(item for item in value if isinstance(item, str) and item)


def _revision_work_package_id(revision: RevisionRequest) -> str:
    return f"revision-{revision.id.hex[:12]}"
