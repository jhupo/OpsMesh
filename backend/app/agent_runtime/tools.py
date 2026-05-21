from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeToolResult,
)
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
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.dependencies import get_docker_runtime_client
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.service import SelfHostedRuntimeService


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
        resolver = ContextualMcpAdapterResolver(
            session=self._session,
            fallback=self._adapter,
            context=context,
            settings=self._settings,
            docker_client=self._docker_client,
        )
        result = McpToolExecutionService(self._session, resolver).execute(
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
        )


class ContextualMcpAdapterResolver:
    def __init__(
        self,
        session: Session,
        fallback: McpToolAdapter | McpToolAdapterResolver,
        context: AgentRuntimeContext,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
    ) -> None:
        self._session = session
        self._fallback = fallback
        self._context = context
        self._settings = settings
        self._docker_client = docker_client

    def resolve(self, server: McpServer) -> McpToolAdapter:
        run = self._current_run()
        runtime = self._runtime_for_run(run)
        if server.server_type != "stdio":
            return self._fallback_adapter(server)
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
            docker_client = self._docker_client or get_docker_runtime_client()
            return DockerRuntimeStdioMcpToolAdapter(
                runtime_manager=RuntimeManager(self._session, docker_client),
                runtime=runtime,
            )
        return self._fallback_adapter(server)

    def _fallback_adapter(self, server: McpServer) -> McpToolAdapter:
        if isinstance(self._fallback, McpToolAdapterResolver):
            return self._fallback.resolve(server)
        return self._fallback

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
        )


def _is_docker_runtime(runtime: WorkspaceRuntime) -> bool:
    return (
        runtime.runtime_provider in {"cloud_docker", "docker"}
        or runtime.runtime_type == "docker"
        or runtime.docker_container_id is not None
    )
