from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeContext, AgentRuntimeToolResult
from backend.app.agent_runtime.errors import normalize_agent_error
from backend.app.agent_runtime.product_tool_executor import (
    PRODUCT_TOOL_NAMES,
    ProductToolExecutor,
)
from backend.app.agent_runtime.tool_gateway import (
    AgentToolGateway,
    PreparedToolCall,
    ToolGatewayDenied,
)
from backend.app.agent_runtime.tool_mcp_resolver import ContextualMcpAdapterResolver
from backend.app.agent_runtime.tool_metadata import product_review_context, tool_metadata
from backend.app.agents.memory_policy import working_memory_policy
from backend.app.approvals.pending_tools import PendingToolInvocationService
from backend.app.approvals.policy import ApprovalPolicyEngine
from backend.app.capabilities.execution import McpToolExecutionService
from backend.app.capabilities.mcp_execution_adapters import (
    McpToolAdapter,
    McpToolAdapterResolver,
)
from backend.app.capabilities.mcp_execution_types import McpExecutionRequest
from backend.app.capabilities.models import McpToolAllowlist
from backend.app.core.config import Settings
from backend.app.files.storage import ObjectStorage
from backend.app.memory.working import AgentWorkingMemoryService
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
        storage: ObjectStorage | None = None,
    ) -> None:
        self._session = session
        self._adapter = adapter
        self._settings = settings
        self._docker_client = docker_client
        self._secret_service = secret_service
        self._storage = storage

    @classmethod
    def for_mcp_adapter(
        cls,
        session: Session,
        adapter: McpToolAdapter | McpToolAdapterResolver,
        *,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
        secret_service: SecretEncryptionService | None = None,
        storage: ObjectStorage | None = None,
    ) -> BackendToolExecutor:
        return cls(
            session,
            adapter,
            settings=settings,
            docker_client=docker_client,
            secret_service=secret_service,
            storage=storage,
        )

    async def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
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
        if not approval_granted or self._secret_service is None:
            return await self._execute_prepared(
                context=context,
                tool_name=tool_name,
                prepared=prepared,
                tool_call_id=tool_call_id,
                approval_granted=approval_granted,
            )
        if tool_call_id is None:
            raise ValueError("Approved tool execution requires a provider tool call ID")
        pending = PendingToolInvocationService(self._session, self._secret_service)
        invocation = pending.by_run_call(
            workspace_id=context.workspace_id,
            run_id=context.run_id,
            tool_call_id=tool_call_id,
        )
        if invocation is None:
            return await self._execute_prepared(
                context=context,
                tool_name=tool_name,
                prepared=prepared,
                tool_call_id=tool_call_id,
                approval_granted=True,
            )
        try:
            invocation, stored = pending.claim_execution(
                workspace_id=context.workspace_id,
                run_id=context.run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
        except ValueError as exc:
            return AgentRuntimeToolResult(
                status="failed",
                error={"code": "approved_tool_call_invalid", "message": str(exc)},
            )
        if stored is not None:
            result = _tool_result_from_payload(stored)
            self._record_working_memory(context, tool_name, tool_call_id, result)
            return result
        try:
            result = await self._execute_prepared(
                context=context,
                tool_name=tool_name,
                prepared=prepared,
                tool_call_id=tool_call_id,
                approval_granted=True,
            )
        except Exception as exc:
            result = AgentRuntimeToolResult(
                status="failed",
                error=normalize_agent_error(exc).as_dict(),
                metadata={"idempotency_key": invocation.idempotency_key},
            )
        pending.complete_execution(invocation, _tool_result_payload(result))
        return result

    def review_tool_call(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        gateway = AgentToolGateway(self._session)
        try:
            prepared = gateway.prepare(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
            )
        except ToolGatewayDenied as exc:
            gateway.record_denial(context=context, tool_name=tool_name, denial=exc)
            raise
        policies = ApprovalPolicyEngine(self._session, self._settings)
        if prepared.definition.source == "product":
            decision = policies.evaluate_product_tool(
                workspace_id=context.workspace_id,
                tool_name=tool_name,
                arguments=prepared.arguments,
                context=product_review_context(context),
            )
        elif prepared.definition.source == "mcp":
            allow = self._mcp_allowlist(prepared)
            decision = policies.evaluate_mcp_tool(
                workspace_id=context.workspace_id,
                tool_name=tool_name,
                arguments=prepared.arguments,
                allowlist_policy=allow.policy,
                allowlist_risk_level=allow.risk_level,
                requires_approval=allow.requires_approval,
                context={
                    "agent_run_id": str(context.run_id),
                    "task_id": str(context.task_id) if context.task_id is not None else None,
                    "mcp_server_id": str(allow.mcp_server_id),
                },
            )
        else:
            raise ToolGatewayDenied(
                "tool_source_invalid",
                "Tool source does not match an executable adapter",
            )
        return decision.approval_payload()

    async def _execute_prepared(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        prepared: PreparedToolCall,
        tool_call_id: str | None,
        approval_granted: bool,
    ) -> AgentRuntimeToolResult:
        if prepared.definition.source == "product" and tool_name in PRODUCT_TOOL_NAMES:
            result = ProductToolExecutor(
                self._session,
                settings=self._settings,
                storage=self._storage,
            ).execute(
                context=context,
                tool_name=tool_name,
                arguments=prepared.arguments,
                resource_grants=prepared.resource_grants,
                approval_granted=approval_granted,
            )
            self._record_working_memory(context, tool_name, tool_call_id, result)
            return result
        if prepared.definition.source != "mcp":
            denial = ToolGatewayDenied(
                "tool_source_invalid",
                "Tool source does not match an executable adapter",
            )
            AgentToolGateway(self._session).record_denial(
                context=context,
                tool_name=tool_name,
                denial=denial,
            )
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
        result = await McpToolExecutionService(
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
                approval_granted=approval_granted,
            )
        )
        tool_result = AgentRuntimeToolResult(
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
        self._record_working_memory(context, tool_name, tool_call_id, tool_result)
        return tool_result

    def _record_working_memory(
        self,
        context: AgentRuntimeContext,
        tool_name: str,
        tool_call_id: str | None,
        result: AgentRuntimeToolResult,
    ) -> None:
        raw_working = context.metadata.get("working_memory")
        raw_policy = raw_working.get("policy") if isinstance(raw_working, dict) else None
        policy = working_memory_policy(
            {"working_memory": raw_policy} if isinstance(raw_policy, dict) else {}
        )
        AgentWorkingMemoryService(self._session).record_tool_result(
            context_workspace_id=context.workspace_id,
            run_id=context.run_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            result=result,
            policy=policy,
        )

    def _mcp_allowlist(self, prepared: PreparedToolCall) -> McpToolAllowlist:
        allowlist_id = prepared.definition.mcp_tool_allowlist_id
        allow = self._session.get(McpToolAllowlist, allowlist_id) if allowlist_id else None
        if allow is None:
            raise ToolGatewayDenied(
                "mcp_tool_allowlist_missing",
                "MCP tool allowlist entry is unavailable",
            )
        return allow


class DisabledToolExecutor:
    def review_tool_call(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        return {
            "decision": "deny",
            "risk_level": "high",
            "reasons": ["tool.executor.unavailable"],
        }

    async def execute_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
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


def _tool_result_payload(result: AgentRuntimeToolResult) -> dict[str, object]:
    return {
        "status": result.status,
        "output": result.output,
        "error": result.error,
        "metadata": result.metadata,
    }


def _tool_result_from_payload(payload: dict[str, object]) -> AgentRuntimeToolResult:
    output = payload.get("output")
    error = payload.get("error")
    metadata = payload.get("metadata")
    return AgentRuntimeToolResult(
        status=str(payload.get("status") or "failed"),
        output=dict(output) if isinstance(output, dict) else None,
        error=dict(error) if isinstance(error, dict) else None,
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


__all__ = [
    "BackendToolExecutor",
    "ContextualMcpAdapterResolver",
    "DisabledToolExecutor",
    "PRODUCT_TOOL_NAMES",
]
