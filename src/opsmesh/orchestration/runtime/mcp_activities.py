"""Native connect/discovery/cleanup with durable lifecycle receipts."""

import asyncio

from sqlalchemy import select
from temporalio import activity

from opsmesh.orchestration.contracts import CleanupScopeAdmission, ScopeCleanupAuthorization
from opsmesh.orchestration.runtime.contracts import MCPCommand, RuntimeResult
from opsmesh.orchestration.runtime.mcp import MCPServerFactory, load_connection
from opsmesh.orchestration.runtime.models import MCPConnection, MCPOperationReceipt
from opsmesh.shared.errors import ConflictError, DomainError


class MCPActivities:
    """Probe and logically manage MCP configurations.

    The official Temporal MCP adapter creates an activity-scoped native
    transport for each tool call. ``ready`` therefore means that the
    connection configuration was successfully probed and its tools frozen;
    it is not a claim that a process or socket remains open between calls.
    """
    def __init__(
        self, factory: MCPServerFactory, cleanup: ScopeCleanupAuthorization | None = None
    ) -> None:
        self.factory = factory
        self.cleanup = cleanup

    def claim(self, command: MCPCommand) -> RuntimeResult | None:
        f = self.factory
        with f.sessions() as session, session.begin():
            admission = f.admission
            if command.cleanup_ref:
                if command.action != "close" or self.cleanup is None:
                    raise ConflictError("The MCP cleanup proof is not assembled")
                admission = CleanupScopeAdmission(self.cleanup, command.cleanup_ref)
            item = load_connection(
                session,
                connection_id=command.connection_id,
                principal_id=command.principal_id,
                admission=admission,
            )
            session.refresh(item, with_for_update=True)
            receipt = session.get(MCPOperationReceipt, (item.scope_id, command.operation_id))
            if receipt is None or receipt.connection_id != item.id:
                raise ConflictError("The MCP operation does not exist")
            if receipt.status == "completed":
                return RuntimeResult(
                    operation_id=receipt.operation_id, status=receipt.status, result=receipt.result
                )
            if receipt.status != "pending":
                raise ConflictError("The MCP operation requires reconciliation")
            if command.action == "close":
                self.factory.assert_connection_idle(session, item.id)
            receipt.status = "started"
            receipt.revision += 1
            item.status = "connecting"
        return None

    def complete(self, command: MCPCommand, tools: list[dict[str, object]] | None) -> RuntimeResult:
        with self.factory.sessions() as session, session.begin():
            item = session.scalars(
                select(MCPConnection)
                .where(MCPConnection.id == command.connection_id)
                .with_for_update()
            ).one()
            receipt = session.get(MCPOperationReceipt, (item.scope_id, command.operation_id))
            if receipt is None:
                raise ConflictError("The MCP operation disappeared")
            if receipt.status == "completed":
                return RuntimeResult(
                    operation_id=receipt.operation_id, status=receipt.status, result=receipt.result
                )
            receipt.status = "uncertain" if tools is None else "completed"
            receipt.revision += 1
            receipt.result = None if tools is None else {"tools": tools}
            item.status = (
                "failed" if tools is None else "closed" if command.action == "close" else "ready"
            )
            if tools is not None:
                item.tools = tools
            return RuntimeResult(
                operation_id=receipt.operation_id, status=receipt.status, result=receipt.result
            )

    @activity.defn(name="orchestration.mcp.lifecycle")
    async def operate(self, command: MCPCommand) -> RuntimeResult:
        try:
            previous = await asyncio.to_thread(self.claim, command)
        except DomainError as exc:
            await asyncio.to_thread(self.reject_pending, command, exc.code)
            raise
        if previous is not None:
            return previous
        try:
            if command.action == "close":
                return await asyncio.to_thread(self.complete, command, [])
            native = self.factory(
                {
                    "connection_id": str(command.connection_id),
                    "principal_id": str(command.principal_id),
                    "lifecycle": command.action,
                }
            )
            try:
                await native.connect()
                tools = [tool.model_dump(mode="json") for tool in await native.list_tools()]
            finally:
                await native.cleanup()
            return await asyncio.to_thread(self.complete, command, tools)
        except BaseException:
            await asyncio.shield(asyncio.to_thread(self.complete, command, None))
            raise

    def reject_pending(self, command: MCPCommand, error_code: str) -> None:
        with self.factory.sessions() as session, session.begin():
            item = session.scalar(
                select(MCPConnection)
                .where(MCPConnection.id == command.connection_id)
                .with_for_update()
            )
            if item is None:
                return
            receipt = session.get(MCPOperationReceipt, (item.scope_id, command.operation_id))
            if receipt is not None and receipt.status == "pending":
                receipt.status = "completed"
                receipt.revision += 1
                receipt.result = {"error_code": error_code, "not_executed": True}
                item.status = "closed" if command.action == "connect" else "ready"
