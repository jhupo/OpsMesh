from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.api.services.exports import WorkspaceExportService
from backend.app.capabilities.adapters import McpAdapterResolver
from backend.app.capabilities.execution import (
    McpExecutionRequest,
    McpToolAdapter,
    McpToolAdapterResolver,
    McpToolExecutionService,
)
from backend.app.core.config import Settings
from backend.app.files.storage import LocalStorage
from backend.app.memory.indexing import WorkspaceMemoryIndexingService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


class WorkerJobHandler:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        agent_runner: AgentRunner | None = None,
        settings: Settings | None = None,
        mcp_adapter: McpToolAdapter | McpToolAdapterResolver | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._agent_runner = agent_runner
        self._settings = settings
        self._mcp_adapter = mcp_adapter

    def handle(self, job: JobPayload) -> None:
        match job.job_type:
            case JobType.AGENT_RUN:
                RunOrchestrationService(
                    self._session,
                    self._queue,
                    self._agent_runner,
                    self._settings,
                ).run_fake_agent(job)
            case JobType.MCP_TOOL_EXECUTION:
                self._handle_mcp_tool_execution(job)
            case JobType.WORKSPACE_ARCHIVE_EXPORT:
                if self._settings is None:
                    raise ValueError("Worker settings are required for workspace archive export")
                WorkspaceExportService(self._session).run_archive_export_job(
                    job=job,
                    storage=LocalStorage(self._settings.storage_root),
                )
            case JobType.MEMORY_INDEX:
                self._handle_memory_index(job)
            case _:
                raise ValueError(f"Unsupported job type: {job.job_type}")

    def _handle_memory_index(self, job: JobPayload) -> None:
        source_type = _required_string(job.routing, "source_type")
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

    def _handle_mcp_tool_execution(self, job: JobPayload) -> None:
        payload = job.routing
        tool_name = _required_string(payload, "tool_name")
        arguments = _dict(payload.get("arguments"))
        runtime_allowed_tools = _string_tuple(payload.get("runtime_allowed_tools"))
        server_id = _optional_uuid(payload.get("mcp_server_id"))
        agent_run_id = _optional_uuid(payload.get("agent_run_id")) or job.resource_id

        McpToolExecutionService(
            self._session,
            self._mcp_adapter or McpAdapterResolver(),
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


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"MCP tool execution job is missing {key}")
    return value.strip()


def _dict(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("MCP tool execution job arguments must be an object")
    return dict(value)


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("MCP tool execution runtime_allowed_tools must be a list")
    return tuple(item for item in value if isinstance(item, str) and item)


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("MCP tool execution UUID fields must be strings")
    return UUID(value)
