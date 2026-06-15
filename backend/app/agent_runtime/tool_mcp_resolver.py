from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeContext
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.capabilities.mcp_stdio_adapters import (
    DockerRuntimeStdioMcpToolAdapter,
    SelfHostedStdioMcpToolAdapter,
)
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings, get_settings
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.service import SelfHostedRuntimeService


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
        if run is not None and runtime is not None and runtime.runtime_provider == "self_hosted":
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


def _is_docker_runtime(runtime: WorkspaceRuntime) -> bool:
    return (
        runtime.runtime_provider in {"cloud_docker", "docker"}
        or runtime.runtime_type == "docker"
        or runtime.docker_container_id is not None
    )
