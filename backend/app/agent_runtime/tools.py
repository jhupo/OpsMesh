from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeContext, AgentRuntimeToolResult
from backend.app.agent_runtime.product_tool_executor import (
    PRODUCT_TOOL_NAMES,
    ProductToolExecutor,
)
from backend.app.agent_runtime.tool_gateway import AgentToolGateway, ToolGatewayDenied
from backend.app.agent_runtime.tool_mcp_resolver import ContextualMcpAdapterResolver
from backend.app.agent_runtime.tool_metadata import tool_metadata
from backend.app.capabilities.execution import McpToolExecutionService
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.capabilities.mcp_execution_types import McpExecutionRequest
from backend.app.core.config import Settings
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.secrets.service import SecretEncryptionService


class BackendToolExecutor:
    def __init__(
        self,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
        *,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
        secret_service: SecretEncryptionService | None = None,
    ) -> None:
        self._session = session
        self._adapter = adapter
        self._settings = settings
        self._docker_client = docker_client
        self._secret_service = secret_service

    @classmethod
    def for_mcp_adapter(
        cls,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
        *,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
        secret_service: SecretEncryptionService | None = None,
    ) -> BackendToolExecutor:
        return cls(
            session,
            adapter,
            settings=settings,
            docker_client=docker_client,
            secret_service=secret_service,
        )

    def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AgentRuntimeToolResult:
        gateway = AgentToolGateway(self._session)
        try:
            prepared = gateway.prepare(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
            )
        except ToolGatewayDenied as exc:
            gateway.record_denial(context=context, tool_name=tool_name, denial=exc)
            return AgentRuntimeToolResult(
                status="failed",
                error={"code": exc.code, "message": str(exc)},
                metadata=tool_metadata(
                    context=context,
                    tool_name=tool_name,
                    tool_kind="blocked",
                ),
            )
        if prepared.definition.source == "product" and tool_name in PRODUCT_TOOL_NAMES:
            return ProductToolExecutor(
                self._session,
                settings=self._settings,
            ).execute(
                context=context,
                tool_name=tool_name,
                arguments=prepared.arguments,
                resource_grants=prepared.resource_grants,
            )
        if prepared.definition.source != "mcp":
            denial = ToolGatewayDenied(
                "tool_source_invalid",
                "Tool source does not match an executable adapter",
            )
            gateway.record_denial(context=context, tool_name=tool_name, denial=denial)
            return AgentRuntimeToolResult(
                status="failed",
                error={"code": denial.code, "message": str(denial)},
            )
        resolver = ContextualMcpAdapterResolver(
            session=self._session,
            default_adapter=self._adapter,
            context=context,
            settings=self._settings,
            docker_client=self._docker_client,
            secret_service=self._secret_service,
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
                arguments=prepared.arguments,
                mcp_server_id=prepared.definition.mcp_server_id,
                runtime_allowed_tools=context.allowed_tools,
            )
        )
        return AgentRuntimeToolResult(
            status=result.status,
            output=result.response,
            error=result.error,
            metadata=tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="mcp",
                extra={
                    "mcp_tool_call_log_id": str(result.log_id),
                    "latency_ms": result.latency_ms,
                },
            ),
        )


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
            metadata=tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="unavailable",
            ),
        )


__all__ = [
    "BackendToolExecutor",
    "ContextualMcpAdapterResolver",
    "DisabledToolExecutor",
    "PRODUCT_TOOL_NAMES",
]
