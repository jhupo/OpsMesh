from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeToolResult,
)
from backend.app.approvals.service import ApprovalService
from backend.app.artifacts.models import Artifact
from backend.app.capabilities.adapters import (
    DockerRuntimeStdioMcpToolAdapter,
    SelfHostedStdioMcpToolAdapter,
)
from backend.app.capabilities.execution import (
    McpExecutionRequest,
    McpToolAdapter,
    McpToolAdapterResolver,
    McpToolExecutionService,
)
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings, get_settings
from backend.app.files.models import WorkspaceFile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.reviews.tool_execution import ToolExecutionReview, ToolExecutionReviewService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.security.redaction import redact_sensitive_text
from backend.app.self_hosted.service import SelfHostedRuntimeService
from backend.app.tasks.models import Task
from backend.app.tasks.service import TaskStateService
from backend.app.tasks.status import TaskStatus
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolResourceNotFoundError
from backend.app.tools.product_tools import ProductToolService

PRODUCT_TOOL_NAMES = frozenset(
    {
        "archive_workspace_memory",
        "get_agent_inbox",
        "list_workspace_files",
        "mark_agent_message_read",
        "read_workspace_file",
        "remember_workspace_memory",
        "search_workspace_memory",
        "send_agent_message",
        "list_agent_thread_messages",
        "write_artifact",
    }
)


class BackendToolExecutor:
    def __init__(
        self,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
        *,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        self._session = session
        self._adapter = adapter
        self._settings = settings
        self._docker_client = docker_client

    @classmethod
    def for_mcp_adapter(
        cls,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
        *,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
    ) -> BackendToolExecutor:
        return cls(session, adapter, settings=settings, docker_client=docker_client)

    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        if tool_name in PRODUCT_TOOL_NAMES:
            return self._execute_product_tool(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
            )
        resolver = ContextualMcpAdapterResolver(
            session=self._session,
            default_adapter=self._adapter,
            context=context,
            settings=self._settings,
            docker_client=self._docker_client,
        )
        result = McpToolExecutionService(
            self._session,
            resolver,
            settings=self._settings,
        ).execute(
            McpExecutionRequest(
                workspace_id=context.workspace_id,
                agent_run_id=context.run_id,
                tool_name=tool_name,
                arguments=arguments,
                runtime_allowed_tools=context.allowed_tools,
            )
        )
        return AgentRuntimeToolResult(
            status=result.status,
            output=result.response,
            error=result.error,
            metadata=_tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="mcp",
                extra={
                    "mcp_tool_call_log_id": str(result.log_id),
                    "latency_ms": result.latency_ms,
                },
            ),
        )

    def _execute_product_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        execution_review = ToolExecutionReviewService(
            self._session,
            self._settings,
        ).review_product_tool_call(
            workspace_id=context.workspace_id,
            tool_name=tool_name,
            arguments=arguments,
            context=_product_review_context(context),
        )
        if not execution_review.approved:
            self._request_product_tool_approval(
                context=context,
                tool_name=tool_name,
                execution_review=execution_review,
            )
            return AgentRuntimeToolResult(
                status="waiting_approval",
                error=None,
                metadata=_tool_metadata(
                    context=context,
                    tool_name=tool_name,
                    tool_kind="product",
                    extra={
                        "review_risk_level": execution_review.risk_level,
                        "review_reasons": execution_review.reasons,
                    },
                ),
            )
        product_context = ToolContext(
            workspace_id=context.workspace_id,
            agent_run_id=context.run_id,
            task_id=context.task_id,
            allowed_tools=frozenset(context.allowed_tools),
            metadata=dict(context.metadata),
        )
        service = ProductToolService(self._session)
        try:
            if tool_name == "send_agent_message":
                output = service.send_agent_message(
                    product_context,
                    recipient_agent_profile_id=_uuid_argument(
                        arguments,
                        "recipient_agent_profile_id",
                    ),
                    body=_str_argument(arguments, "body", default=""),
                    thread_id=_optional_uuid_argument(arguments, "thread_id"),
                    subject=_optional_str_argument(arguments, "subject"),
                    message_type=_str_argument(arguments, "message_type", default="message"),
                    payload=_dict_argument(arguments, "payload"),
                    reply_to_message_id=_optional_uuid_argument(
                        arguments,
                        "reply_to_message_id",
                    ),
                )
            elif tool_name == "list_agent_thread_messages":
                output = service.list_agent_thread_messages(
                    product_context,
                    thread_id=_uuid_argument(arguments, "thread_id"),
                    limit=_int_argument(arguments, "limit", default=50),
                    offset=_int_argument(arguments, "offset", default=0),
                    status=_optional_str_argument(arguments, "status"),
                )
            elif tool_name == "get_agent_inbox":
                output = service.get_agent_inbox(
                    product_context,
                    latest_limit=_int_argument(arguments, "latest_limit", default=20),
                    unread_only=_bool_argument(arguments, "unread_only", default=False),
                )
            elif tool_name == "mark_agent_message_read":
                output = service.mark_agent_message_read(
                    product_context,
                    message_id=_uuid_argument(arguments, "message_id"),
                )
            elif tool_name == "search_workspace_memory":
                output = {
                    "items": service.search_workspace_memory(
                        product_context,
                        query=_str_argument(arguments, "query", default=""),
                        limit=_int_argument(arguments, "limit", default=10),
                        source_types=_optional_str_set_argument(arguments, "source_types"),
                    )
                }
            elif tool_name == "remember_workspace_memory":
                output = _memory_entry_payload(
                    service.remember_workspace_memory(
                        product_context,
                        title=_str_argument(arguments, "title", default="Untitled memory"),
                        content=_str_argument(arguments, "content", default=""),
                        entry_type=_str_argument(arguments, "entry_type", default="note"),
                        tags=_str_list_argument(arguments, "tags"),
                        source_type=_optional_str_argument(arguments, "source_type"),
                        source_id=_optional_str_argument(arguments, "source_id"),
                        visibility_scope=_str_argument(
                            arguments,
                            "visibility_scope",
                            default="workspace",
                        ),
                        importance=_int_argument(arguments, "importance", default=0),
                        metadata=_dict_argument(arguments, "metadata"),
                    )
                )
            elif tool_name == "archive_workspace_memory":
                output = _memory_entry_payload(
                    service.archive_workspace_memory(
                        product_context,
                        memory_entry_id=_uuid_argument(arguments, "memory_entry_id"),
                    )
                )
            elif tool_name == "list_workspace_files":
                output = {
                    "items": [
                        _workspace_file_payload(file)
                        for file in service.list_workspace_files(product_context)
                    ]
                }
            elif tool_name == "read_workspace_file":
                output = _workspace_file_payload(
                    service.read_workspace_file(
                        product_context,
                        file_id=_uuid_argument(arguments, "file_id"),
                    )
                )
            elif tool_name == "write_artifact":
                output = _artifact_payload(
                    service.write_artifact(
                        product_context,
                        filename=_str_argument(arguments, "filename", default="artifact.txt"),
                        content=_bytes_argument(arguments, "content"),
                        content_type=_str_argument(
                            arguments,
                            "content_type",
                            default="text/plain",
                        ),
                        artifact_type=_str_argument(arguments, "artifact_type", default="file"),
                    )
                )
            else:  # pragma: no cover - guarded by PRODUCT_TOOL_NAMES.
                raise ValueError(f"Unsupported product tool: {tool_name}")
        except (ToolResourceNotFoundError, ValueError) as exc:
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": "product_tool_failed",
                    "message": redact_sensitive_text(str(exc)),
                },
                metadata=_tool_metadata(
                    context=context,
                    tool_name=tool_name,
                    tool_kind="product",
                ),
            )
        self._session.commit()
        return AgentRuntimeToolResult(
            status="completed",
            output=output,
            metadata=_tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="product",
            ),
        )

    def _request_product_tool_approval(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        execution_review: ToolExecutionReview,
    ) -> None:
        ApprovalService(self._session).create_approval(
            workspace_id=context.workspace_id,
            task_id=context.task_id,
            agent_run_id=context.run_id,
            requested_by_agent_profile_id=_agent_profile_id_for_context(
                self._session,
                context,
            ),
            approval_type="product.tool",
            risk_level=execution_review.risk_level,
            payload={
                "tool_name": tool_name,
                "reason": "product_tool_execution_review_requires_approval",
                "execution_review": execution_review.approval_payload(),
            },
        )
        run = self._session.get(AgentRun, context.run_id)
        if run is not None and run.workspace_id == context.workspace_id:
            run.status = RunStatus.WAITING_APPROVAL.value
        if context.task_id is not None:
            task = self._session.get(Task, context.task_id)
            if (
                task is not None
                and task.workspace_id == context.workspace_id
                and task.status == TaskStatus.RUNNING.value
            ):
                TaskStateService().transition(task, TaskStatus.WAITING_APPROVAL)
        self._session.commit()


class ContextualMcpAdapterResolver:
    def __init__(
        self,
        session: Session,
        default_adapter: McpToolAdapter | McpToolAdapterResolver,
        context: AgentRuntimeContext,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        self._session = session
        self._default_adapter = default_adapter
        self._context = context
        self._settings = settings
        self._docker_client = docker_client

    def resolve(self, server: McpServer) -> McpToolAdapter:
        run = self._current_run()
        runtime = self._runtime_for_run(run)
        if server.server_type != "stdio":
            return self._default_adapter_for(server)
        if (
            run is not None
            and runtime is not None
            and runtime.runtime_provider == "self_hosted"
        ):
            return SelfHostedStdioMcpToolAdapter(
                service=SelfHostedRuntimeService(self._session, self._settings or get_settings()),
                runtime=runtime,
                agent_run_id=run.id,
            )
        if runtime is not None and _is_docker_runtime(runtime):
            if self._docker_client is None:
                raise RuntimeError("Docker runtime MCP execution requires a worker-injected client")
            return DockerRuntimeStdioMcpToolAdapter(
                runtime_manager=RuntimeManager(self._session, self._docker_client),
                runtime=runtime,
            )
        return self._default_adapter_for(server)

    def _default_adapter_for(self, server: McpServer) -> McpToolAdapter:
        if isinstance(self._default_adapter, McpToolAdapterResolver):
            return self._default_adapter.resolve(server)
        return self._default_adapter

    def _current_run(self) -> AgentRun | None:
        run = self._session.get(AgentRun, self._context.run_id)
        if run is None or run.workspace_id != self._context.workspace_id:
            return None
        return run

    def _runtime_for_run(self, run: AgentRun | None) -> WorkspaceRuntime | None:
        if run is None or run.runtime_id is None:
            return None
        runtime = self._session.get(WorkspaceRuntime, run.runtime_id)
        if runtime is None or runtime.workspace_id != run.workspace_id:
            return None
        return runtime


class DisabledToolExecutor:
    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        return AgentRuntimeToolResult(
            status="failed",
            error={
                "code": "tool_executor_unavailable",
                "message": f"Tool executor is not configured for {tool_name}",
            },
            metadata=_tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="unavailable",
            ),
        )


def _is_docker_runtime(runtime: WorkspaceRuntime) -> bool:
    return (
        runtime.runtime_provider in {"cloud_docker", "docker"}
        or runtime.runtime_type == "docker"
        or runtime.docker_container_id is not None
    )


def _tool_metadata(
    *,
    context: AgentRuntimeContext,
    tool_name: str,
    tool_kind: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "provenance": "backend_tool_executor",
        "tool_name": tool_name,
        "tool_kind": tool_kind,
        "workspace_id": str(context.workspace_id),
        "run_id": str(context.run_id),
        "task_id": str(context.task_id) if context.task_id is not None else None,
        "user_id": str(context.user_id) if context.user_id is not None else None,
    }
    context_metadata = context.metadata if isinstance(context.metadata, dict) else {}
    for key in ("trace_id", "span_id", "persistent_session_key"):
        value = context_metadata.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    team_context = context_metadata.get("team_context")
    if isinstance(team_context, dict):
        team_metadata: dict[str, object] = {}
        for key in ("team_id", "team_name", "team_type"):
            value = team_context.get(key)
            if isinstance(value, str) and value:
                team_metadata[key] = value
        current_member = team_context.get("current_member")
        if isinstance(current_member, dict):
            member_metadata = {
                key: value
                for key, value in current_member.items()
                if key
                in {
                    "member_id",
                    "agent_profile_id",
                    "reports_to_member_id",
                    "team_role",
                    "department",
                    "position_title",
                }
                and isinstance(value, str)
            }
            if member_metadata:
                team_metadata["current_member"] = member_metadata
        runtime = team_context.get("runtime")
        if isinstance(runtime, dict):
            runtime_metadata: dict[str, object] = {}
            for key in (
                "status",
                "workspace_runtime_id",
                "runtime_status",
                "runtime_space_id",
                "thread_id",
                "team_session_id",
            ):
                value = runtime.get(key)
                if isinstance(value, str) and value:
                    runtime_metadata[key] = value
            member_session_count = runtime.get("member_session_count")
            if isinstance(member_session_count, int) and not isinstance(
                member_session_count,
                bool,
            ):
                runtime_metadata["member_session_count"] = member_session_count
            if runtime_metadata:
                team_metadata["runtime"] = runtime_metadata
        if team_metadata:
            metadata["team"] = team_metadata
    if extra:
        metadata.update(extra)
    return metadata


def _product_review_context(context: AgentRuntimeContext) -> dict[str, object]:
    return {
        "agent_run_id": str(context.run_id),
        "task_id": str(context.task_id) if context.task_id is not None else None,
        "allowed_tools": list(context.allowed_tools),
        "metadata": dict(context.metadata),
    }


def _agent_profile_id_for_context(
    session: Session,
    context: AgentRuntimeContext,
) -> UUID | None:
    run = session.get(AgentRun, context.run_id)
    if run is None or run.workspace_id != context.workspace_id:
        return None
    return run.agent_profile_id


def _uuid_argument(arguments: dict[str, object], key: str) -> UUID:
    value = arguments.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    raise ValueError(f"Missing UUID argument: {key}")


def _optional_uuid_argument(arguments: dict[str, object], key: str) -> UUID | None:
    value = arguments.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError(f"Invalid UUID argument: {key}")


def _str_argument(arguments: dict[str, object], key: str, *, default: str) -> str:
    value = arguments.get(key, default)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    raise ValueError(f"Invalid string argument: {key}")


def _optional_str_argument(arguments: dict[str, object], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError(f"Invalid string argument: {key}")


def _dict_argument(arguments: dict[str, object], key: str) -> dict[str, object]:
    value = arguments.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(item_key): item_value for item_key, item_value in value.items()}
    raise ValueError(f"Invalid object argument: {key}")


def _str_list_argument(arguments: dict[str, object], key: str) -> list[str]:
    value = arguments.get(key)
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return [item for item in value if isinstance(item, str)]
    raise ValueError(f"Invalid string list argument: {key}")


def _optional_str_set_argument(arguments: dict[str, object], key: str) -> set[str] | None:
    items = _str_list_argument(arguments, key)
    return set(items) if items else None


def _bytes_argument(arguments: dict[str, object], key: str) -> bytes:
    value = arguments.get(key)
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ValueError(f"Invalid bytes argument: {key}")


def _int_argument(arguments: dict[str, object], key: str, *, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"Invalid integer argument: {key}")
    if isinstance(value, int):
        return value
    raise ValueError(f"Invalid integer argument: {key}")


def _bool_argument(arguments: dict[str, object], key: str, *, default: bool) -> bool:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"Invalid boolean argument: {key}")


def _memory_entry_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": str(entry.id),
        "entry_type": entry.entry_type,
        "title": entry.title,
        "status": entry.status,
        "visibility_scope": entry.visibility_scope,
        "importance": entry.importance,
        "tags": list(entry.tags or []),
    }


def _workspace_file_payload(file: WorkspaceFile) -> dict[str, object]:
    return {
        "id": str(file.id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "status": file.status,
    }


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "artifact_type": artifact.artifact_type,
        "review_status": artifact.review_status,
    }
