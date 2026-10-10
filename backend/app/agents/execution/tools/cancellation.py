"""Durable cancellation intent distinguishes queued work from external work already claimed."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeContext
from backend.app.capabilities.mcp.execution.events import McpToolCallLogService
from backend.app.governance.audit.service import AuditService
from backend.app.orchestration.runs.events import RunEventRecorder
from backend.app.orchestration.runs.models import AgentRun
from backend.app.runtime.self_hosted.models import SelfHostedMcpJob


def request_tool_cancellation(session: Session, context: AgentRuntimeContext) -> None:
    run = session.scalar(
        select(AgentRun).where(
            AgentRun.workspace_id == context.workspace_id,
            AgentRun.id == context.run_id,
        )
    )
    if run is None:
        raise ValueError("Cancellation run is outside its workspace")
    jobs = session.scalars(
        select(SelfHostedMcpJob)
        .where(
            SelfHostedMcpJob.workspace_id == context.workspace_id,
            SelfHostedMcpJob.agent_run_id == context.run_id,
            SelfHostedMcpJob.status.in_(("queued", "claimed")),
        )
        .with_for_update()
    ).all()
    queued, external = 0, 0
    now = datetime.now(UTC)
    for job in jobs:
        if job.cancel_requested_at is not None:
            continue
        job.cancel_requested_at = now
        if job.status == "queued":
            queued += 1
            job.status = "failed"
            job.completed_at = now
            job.error_payload = {"code": "run_cancelled", "message": "Cancelled before dispatch"}
            McpToolCallLogService(session).complete_runtime_rpc(
                workspace_id=context.workspace_id,
                run_id=context.run_id,
                job_id=job.id,
                status="failed",
                response=None,
                error=job.error_payload,
            )
        else:
            external += 1
    metadata: dict[str, object] = {
        "queued_operations_cancelled": queued,
        "external_operations_awaiting_completion": external,
    }
    RunEventRecorder(session).append_event(
        run,
        "tool.cancellation_requested",
        "Tool cancellation intent recorded",
        metadata,
    )
    AuditService(session).record_system_action(
        workspace_id=context.workspace_id,
        action="tool.cancellation_requested",
        target_type="agent_run",
        target_id=context.run_id,
        metadata=metadata,
    )
