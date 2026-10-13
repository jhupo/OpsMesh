"""Authorize and execute product tools inside a Temporal Activity.

MCP transport is owned by the official Temporal OpenAI Agents integration. This
module intentionally contains no transport client, queue, polling, or runtime
container code.
"""

from __future__ import annotations

from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentExecutionContext, ToolExecutionResult
from opsmesh.agents.execution.tools.authorization import (
    AgentToolAccessDeniedError,
    AgentToolAuthorizer,
    AuthorizedToolCall,
)
from opsmesh.agents.execution.tools.metadata import product_review_context, tool_metadata
from opsmesh.agents.execution.tools.product import PRODUCT_TOOL_NAMES, ProductToolDispatcher
from opsmesh.capabilities.mcp.models import MCPToolAllowlist
from opsmesh.capabilities.tools.contracts import ToolPermissionError
from opsmesh.orchestration.approvals.policy import ApprovalPolicyEngine
from opsmesh.resources.storage.backends import ObjectStorage
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService


class AgentToolExecutionService:
    """Execute the product-owned tool catalog from an Activity.

    The constructor deliberately accepts only dependencies needed by product
    tools. A model-issued MCP call must be handled by ``temporal_mcp_server``
    and never reaches this class.
    """

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        secret_service: SecretEncryptionService | None = None,
        storage: ObjectStorage | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._secret_service = secret_service
        self._storage = storage

    def execute_tool(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        tool_call_id: str | None = None,
        approval_granted: bool = False,
    ) -> ToolExecutionResult:
        del tool_call_id
        try:
            result = self._execute_product(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
                approval_granted=approval_granted,
            )
            self._session.commit()
            return result
        except AgentToolAccessDeniedError as exc:
            self._session.commit()
            return ToolExecutionResult(
                status="failed",
                error={"code": exc.code, "message": str(exc)},
                metadata=tool_metadata(
                    context=context,
                    tool_name=tool_name,
                    tool_kind="blocked",
                ),
            )
        except ToolPermissionError:
            self._session.commit()
            raise
        except BaseException:
            self._session.rollback()
            raise

    def _execute_product(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        approval_granted: bool,
    ) -> ToolExecutionResult:
        prepared = self._prepare_product(context, tool_name, arguments)
        if tool_name not in PRODUCT_TOOL_NAMES:
            self._deny_native_mcp(context, tool_name)
        result = ProductToolDispatcher(
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
        if not isinstance(result, ToolExecutionResult):
            raise TypeError("Product tool executor returned an unsupported result")
        return result

    def _prepare_product(
        self,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> AuthorizedToolCall:
        gateway = AgentToolAuthorizer(self._session)
        try:
            prepared = gateway.prepare(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
            )
        except AgentToolAccessDeniedError as exc:
            gateway.record_denial(context=context, tool_name=tool_name, denial=exc)
            raise
        if prepared.definition.source != "product":
            self._deny_native_mcp(context, tool_name)
        return prepared

    def review_tool_call(
        self,
        *,
        context: AgentExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        """Evaluate product or native MCP approval policy without executing it."""
        gateway = AgentToolAuthorizer(self._session)
        try:
            prepared = gateway.prepare(
                context=context,
                tool_name=tool_name,
                arguments=arguments,
            )
        except AgentToolAccessDeniedError as exc:
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
            self._deny_native_mcp(context, tool_name)
        return decision.approval_payload()

    def _mcp_allowlist(
        self,
        prepared: AuthorizedToolCall,
        *,
        workspace_id: UUID,
    ) -> MCPToolAllowlist:
        allowlist_id = prepared.definition.mcp_tool_allowlist_id
        allow = (
            self._session.scalar(
                select(MCPToolAllowlist).where(
                    MCPToolAllowlist.workspace_id == workspace_id,
                    MCPToolAllowlist.id == allowlist_id,
                    MCPToolAllowlist.mcp_server_id == prepared.definition.mcp_server_id,
                )
            )
            if allowlist_id is not None
            else None
        )
        if allow is None:
            raise AgentToolAccessDeniedError(
                "mcp_tool_allowlist_missing",
                "MCP tool allowlist entry is unavailable",
            )
        return allow

    @staticmethod
    def _deny_native_mcp(context: AgentExecutionContext, tool_name: str) -> NoReturn:
        del context
        raise AgentToolAccessDeniedError(
            "native_mcp_execution_required",
            f"MCP tool {tool_name} must execute through the Temporal MCP integration",
        )


__all__ = ["AgentToolExecutionService", "PRODUCT_TOOL_NAMES"]
