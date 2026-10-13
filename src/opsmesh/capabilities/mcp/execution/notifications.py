"""Allowlisted SDK notification evidence, never raw protocol or stderr text."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import UUID

from mcp.client.session import IncomingMessage
from mcp.types import LoggingMessageNotification
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.execution.diagnostics import (
    DIAGNOSTIC_META_KEY,
    MCPErrorDiagnostic,
    native_failure,
)
from opsmesh.capabilities.mcp.execution.events import MCPExecutionNotifier
from opsmesh.capabilities.mcp.models import MCPToolCallLog
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.statuses import ACTIVE_RUN_STATUS_VALUES
from opsmesh.shared.telemetry.metrics import metrics_registry

logger = logging.getLogger(__name__)


class MCPNotificationRecorder:
    """Only the trusted factory supplies tenant, run and server identities."""

    def __init__(
        self,
        sessions: Callable[[], AbstractContextManager[Session]],
        *,
        workspace_id: UUID,
        run_id: UUID,
        server_id: UUID,
    ) -> None:
        self._sessions = sessions
        self._workspace_id, self._run_id, self._server_id = workspace_id, run_id, server_id
        self._accepted = 0

    async def notify(self, message: IncomingMessage) -> None:
        if self._accepted >= 128:
            return
        if isinstance(message, Exception):
            data = {DIAGNOSTIC_META_KEY: {**native_failure(message), "phase": "connect"}}
        elif isinstance(message, LoggingMessageNotification):
            data = message.params.data
        else:
            return
        if not isinstance(data, dict):
            return
        payload = data.get(DIAGNOSTIC_META_KEY)
        if not isinstance(payload, dict):
            return
        try:
            diagnostic = MCPErrorDiagnostic.model_validate(payload, strict=True)
        except ValidationError:
            return
        if not (diagnostic.exception_type or diagnostic.http_status or diagnostic.operation_id):
            return
        self._accepted += 1
        call_id = data.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 160:
            call_id = None
        try:
            await asyncio.to_thread(self._record, diagnostic, call_id)
        except Exception as failure:
            # A diagnostic callback must never interrupt protocol execution or let
            # the SDK log the untrusted notification inside a callback traceback.
            metrics_registry.increment(
                "opsmesh_mcp_diagnostic_persistence_failures_total",
                labels={"reason": "database_write"},
                help_text="Native MCP diagnostic notifications that could not be persisted.",
            )
            logger.warning(
                "Native MCP diagnostic evidence could not be persisted",
                extra={
                    "event": "mcp.diagnostic.persist_failed",
                    "exception_type": type(failure).__name__,
                },
            )

    def _record(self, diagnostic: MCPErrorDiagnostic, call_id: str | None) -> None:
        with self._sessions() as session:
            run = session.scalar(
                select(AgentRun).where(
                    AgentRun.workspace_id == self._workspace_id,
                    AgentRun.id == self._run_id,
                    AgentRun.status.in_(ACTIVE_RUN_STATUS_VALUES),
                )
            )
            if run is None:
                return
            log = (
                session.scalar(
                    select(MCPToolCallLog)
                    .where(
                        MCPToolCallLog.workspace_id == self._workspace_id,
                        MCPToolCallLog.agent_run_id == self._run_id,
                        MCPToolCallLog.mcp_server_id == self._server_id,
                        MCPToolCallLog.tool_call_id == call_id,
                        MCPToolCallLog.status == "running",
                    )
                    .with_for_update()
                )
                if call_id is not None
                else None
            )
            evidence = diagnostic.model_dump(mode="json", exclude_none=True)
            if log is not None:
                response = dict(log.response or {})
                raw_metadata = response.get("_meta")
                metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                existing = metadata.get(DIAGNOSTIC_META_KEY)
                private = dict(existing) if isinstance(existing, dict) else {}
                notifications = list(private.get("notifications") or [])
                if len(notifications) >= 32:
                    return
                private["notifications"] = [*notifications, evidence]
                metadata[DIAGNOSTIC_META_KEY] = private
                log.response = {**response, "_meta": metadata}
            MCPExecutionNotifier(session).append_run_event(
                run=run,
                event_type="mcp.diagnostic.notification",
                message="Native MCP diagnostic notification received",
                metadata={
                    "mcp_server_id": str(self._server_id),
                    "scope": "tool_call" if log is not None else "connection",
                    "mcp_tool_call_log_id": str(log.id) if log is not None else None,
                    "tool_call_id": call_id if log is not None else None,
                    "diagnostic": evidence,
                },
            )
            session.commit()
