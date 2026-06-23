from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.capabilities.mcp_execution_context import snapshot_audit_metadata
from backend.app.capabilities.mcp_execution_types import McpExecutionRequest
from backend.app.capabilities.mcp_payloads import canonical_payload, error_code, payload_hash
from backend.app.capabilities.mcp_payloads import response_hash as mcp_response_hash
from backend.app.capabilities.models import McpToolCallLog
from backend.app.runs.models import AgentRun


@dataclass(slots=True)
class McpToolCallLogService:
    session: Session

    def record(
        self,
        *,
        request: McpExecutionRequest,
        server_id: UUID | None,
        status: str,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        snapshot: dict[str, object] | None = None,
        run: AgentRun | None = None,
        latency_ms: int | None = None,
    ) -> McpToolCallLog:
        argument_sha256 = payload_hash(request.arguments)
        response_sha256 = mcp_response_hash(response)
        log = McpToolCallLog(
            workspace_id=request.workspace_id,
            mcp_server_id=server_id,
            agent_run_id=request.agent_run_id,
            task_id=run.task_id if run is not None else None,
            task_step_id=run.task_step_id if run is not None else None,
            agent_profile_id=run.agent_profile_id if run is not None else None,
            tool_name=request.tool_name,
            status=status,
            latency_ms=latency_ms,
            argument_sha256=argument_sha256,
            response_sha256=response_sha256,
            error_code=error_code(error),
            request={
                "arguments_sha256": argument_sha256,
                "argument_bytes": len(canonical_payload(request.arguments).encode("utf-8")),
                **snapshot_audit_metadata(snapshot or {}),
            },
            response=response,
            error=error,
            created_at=datetime.now(UTC),
        )
        self.session.add(log)
        self.session.flush()
        return log
