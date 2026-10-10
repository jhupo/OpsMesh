from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeContext, AgentRuntimeToolResult
from backend.app.agents.execution.errors import normalize_agent_error
from backend.app.agents.execution.tools.gateway import (
    AgentToolGateway,
    PreparedToolCall,
    ToolGatewayDenied,
)
from backend.app.agents.execution.tools.mcp import ContextualMcpAdapterResolver
from backend.app.agents.execution.tools.metadata import product_review_context, tool_metadata
from backend.app.agents.execution.tools.product import PRODUCT_TOOL_NAMES, ProductToolExecutor
from backend.app.capabilities.mcp.execution.contracts import McpExecutionRequest
from backend.app.capabilities.mcp.execution.invocation import McpToolInvoker
from backend.app.capabilities.mcp.execution.prepared import (
    PreparedMcpExecution,
    PreparedMcpTool,
    ToolPreparation,
)
from backend.app.capabilities.mcp.execution.service import McpToolExecutionService
from backend.app.capabilities.mcp.models import McpToolAllowlist
from backend.app.capabilities.tools.contracts import ToolPermissionError
from backend.app.orchestration.approvals.models import PendingToolInvocation
from backend.app.orchestration.approvals.pending_tools import PendingToolInvocationService
from backend.app.orchestration.approvals.policy import ApprovalPolicyEngine
from backend.app.resources.storage.storage import ObjectStorage
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.queues.execution_control import (
    ExecutionOwnershipLostError,
    current_execution_control,
)
from backend.app.runtime.self_hosted.models import SelfHostedMcpJob
from backend.app.shared.config import Settings
from backend.app.shared.security.secrets import SecretEncryptionService


class BackendToolExecutor:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        docker_client: DockerRuntimeClient | None = None,
        secret_service: SecretEncryptionService | None = None,
        storage: ObjectStorage | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._docker_client = docker_client
        self._secret_service = secret_service
        self._storage = storage

    def prepare_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> ToolPreparation:
        try:
            result = self._prepare_tool(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
                tool_call_id=tool_call_id,
                approval_granted=approval_granted,
            )
            control = current_execution_control()
            if control is not None:
                control.check_ownership()
            self._session.commit()
            return result
        except ToolPermissionError:
            control = current_execution_control()
            if control is not None:
                control.check_ownership()
            self._session.commit()
            raise
        except BaseException:
            self._session.rollback()
            raise

    def _prepare_tool(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> ToolPreparation:
        control = current_execution_control()
        if control is not None:
            control.check_ownership()
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
            existing = self._runtime_rpc_result(context, prepared, tool_name, tool_call_id)
            if existing is not None:
                return existing
            return self._execute_prepared(
                context=context,
                tool_name=tool_name,
                prepared=prepared,
                tool_call_id=tool_call_id,
                approval_granted=False,
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
            existing = self._runtime_rpc_result(context, prepared, tool_name, tool_call_id)
            if existing is not None:
                return existing
            return self._execute_prepared(
                context=context,
                tool_name=tool_name,
                prepared=prepared,
                tool_call_id=tool_call_id,
                approval_granted=False,
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
            # A durable RPC may be reattached by its original call ID. Unknown
            # local side effects still fail closed and are never replayed.
            rpc_result = self._runtime_rpc_result(context, prepared, tool_name, tool_call_id)
            if invocation.status not in {"executing", "outcome_unknown"} or rpc_result is None:
                return _tool_result_from_payload(stored)
            if rpc_result.status != "waiting_self_hosted":
                pending.complete_execution(invocation, tool_result_payload(rpc_result))
            return rpc_result
        try:
            result = self._execute_prepared(
                context=context,
                tool_name=tool_name,
                prepared=prepared,
                tool_call_id=tool_call_id,
                approval_granted=True,
            )
        except ExecutionOwnershipLostError:
            raise
        except Exception as exc:
            result = AgentRuntimeToolResult(
                status="failed",
                error=normalize_agent_error(exc).as_dict(),
                metadata={"idempotency_key": invocation.idempotency_key},
            )
        control = current_execution_control()
        if control is not None:
            control.check_ownership()
        if isinstance(result, PreparedMcpTool):
            return replace(result, pending_invocation_id=invocation.id)
        if result.status != "waiting_self_hosted":
            pending.complete_execution(invocation, tool_result_payload(result))
        return result

    def _runtime_rpc_result(
        self,
        context: AgentRuntimeContext,
        prepared: PreparedToolCall,
        tool_name: str,
        tool_call_id: str | None,
    ) -> AgentRuntimeToolResult | None:
        if tool_call_id is None or prepared.definition.source != "mcp":
            return None
        rpc = self._session.scalar(
            select(SelfHostedMcpJob).where(
                SelfHostedMcpJob.workspace_id == context.workspace_id,
                SelfHostedMcpJob.agent_run_id == context.run_id,
                SelfHostedMcpJob.tool_call_id == tool_call_id,
                SelfHostedMcpJob.tool_name == tool_name,
            )
        )
        if rpc is None:
            return None
        request = rpc.request_payload.get("request")
        tool = request.get("tool") if isinstance(request, dict) else None
        if (
            not isinstance(tool, dict)
            or tool.get("arguments") != prepared.arguments
            or rpc.mcp_server_id != prepared.definition.mcp_server_id
            or context.runtime_binding is None
            or rpc.workspace_runtime_id != context.runtime_binding.workspace_runtime_id
        ):
            raise ValueError("Durable Runtime RPC does not match the authorized tool invocation")
        if rpc.status in {"queued", "claimed"}:
            return AgentRuntimeToolResult(
                status="waiting_self_hosted", output={"mcp_job_id": str(rpc.id)}
            )
        return AgentRuntimeToolResult(
            status="completed" if rpc.status == "completed" else "failed",
            output=rpc.response_payload,
            error=rpc.error_payload,
        )

    def review_tool_call(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        control = current_execution_control()
        if control is not None:
            control.check_ownership()
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
            allow = self._mcp_allowlist(prepared, workspace_id=context.workspace_id)
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

    def _execute_prepared(
        self,
        *,
        context: AgentRuntimeContext,
        tool_name: str,
        prepared: PreparedToolCall,
        tool_call_id: str | None,
        approval_granted: bool,
    ) -> ToolPreparation:
        if prepared.definition.source == "product" and tool_name in PRODUCT_TOOL_NAMES:
            result = ProductToolExecutor(
                self._session,
                settings=self._settings,
                storage=self._storage,
                secret_service=self._secret_service,
            ).execute(
                context=context,
                tool_name=tool_name,
                arguments=prepared.arguments,
                resource_grants=prepared.resource_grants,
                approval_granted=approval_granted,
            )
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
            context=replace(
                context, metadata={**context.metadata, "sdk_tool_call_id": tool_call_id}
            ),
            settings=self._settings,
            docker_client=self._docker_client,
            secret_service=self._secret_service,
        )
        mcp_result = McpToolExecutionService(
            self._session,
            resolver,
            settings=self._settings,
        ).prepare(
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
        if isinstance(mcp_result, PreparedMcpExecution):
            return PreparedMcpTool(
                mcp_result, tool_metadata(context=context, tool_name=tool_name, tool_kind="mcp")
            )
        tool_result = AgentRuntimeToolResult(
            status=mcp_result.status,
            output=mcp_result.response,
            error=mcp_result.error,
            metadata=tool_metadata(
                context=context,
                tool_name=tool_name,
                tool_kind="mcp",
                extra={
                    "mcp_tool_call_log_id": str(mcp_result.log_id),
                    "latency_ms": mcp_result.latency_ms,
                },
            ),
        )
        return tool_result

    def complete_tool(
        self,
        prepared: PreparedMcpTool,
        response: dict[str, object] | None,
        error: BaseException | None,
    ) -> AgentRuntimeToolResult:
        mcp = McpToolInvoker(self._session, None).complete(prepared.execution, response, error)
        result = AgentRuntimeToolResult(
            status=mcp.status,
            output=mcp.response,
            error=mcp.error,
            metadata={
                **prepared.metadata,
                "mcp_tool_call_log_id": str(mcp.log_id),
                "latency_ms": mcp.latency_ms,
            },
        )
        if prepared.pending_invocation_id is not None:
            invocation = self._session.get(PendingToolInvocation, prepared.pending_invocation_id)
            if (
                invocation is None
                or invocation.workspace_id != prepared.execution.request.workspace_id
                or invocation.agent_run_id != prepared.execution.request.agent_run_id
            ):
                raise ValueError("Pending tool completion is outside its workspace")
            if self._secret_service is None:
                raise ValueError("Tool completion requires its secret service")
            PendingToolInvocationService(self._session, self._secret_service).complete_execution(
                invocation, tool_result_payload(result)
            )
        return result

    def _mcp_allowlist(self, prepared: PreparedToolCall, *, workspace_id: UUID) -> McpToolAllowlist:
        allowlist_id = prepared.definition.mcp_tool_allowlist_id
        allow = (
            self._session.scalar(
                select(McpToolAllowlist).where(
                    McpToolAllowlist.workspace_id == workspace_id,
                    McpToolAllowlist.id == allowlist_id,
                    McpToolAllowlist.mcp_server_id == prepared.definition.mcp_server_id,
                )
            )
            if allowlist_id is not None
            else None
        )
        if allow is None:
            raise ToolGatewayDenied(
                "mcp_tool_allowlist_missing",
                "MCP tool allowlist entry is unavailable",
            )
        return allow


class DisabledToolExecutor:
    async def cancel_active_tools(self, *, context: AgentRuntimeContext) -> None:
        # This executor cannot start an operation; cancellation has nothing to stop.
        return None

    async def review_tool_call(
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


def tool_result_payload(result: AgentRuntimeToolResult) -> dict[str, object]:
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
