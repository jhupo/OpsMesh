from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import (
    AgentRuntimeContext,
    AgentRuntimeToolResult,
)
from backend.app.capabilities.adapters import SelfHostedStdioMcpToolAdapter
from backend.app.capabilities.execution import (
    McpExecutionRequest,
    McpToolAdapter,
    McpToolAdapterResolver,
    McpToolExecutionService,
)
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings, get_settings
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.service import SelfHostedRuntimeService


class BackendToolExecutor:
    def __init__(
        self,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._adapter = adapter
        self._settings = settings

    @classmethod
    def for_mcp_adapter(
        cls,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
    ) -> BackendToolExecutor:
        return cls(session, adapter)

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
    ) -> None:
        self._session = session
        self._fallback = fallback
        self._context = context
        self._settings = settings

    def resolve(self, server: McpServer) -> McpToolAdapter:
        run = self._current_run()
        runtime = self._runtime_for_run(run)
        if (
            run is not None
            and runtime is not None
            and runtime.runtime_provider == "self_hosted"
            and server.server_type == "stdio"
        ):
            return SelfHostedStdioMcpToolAdapter(
                service=SelfHostedRuntimeService(self._session, self._settings or get_settings()),
                runtime=runtime,
                agent_run_id=run.id,
            )
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
