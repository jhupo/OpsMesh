"""Record product-tool cancellation from the Temporal execution boundary."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentExecutionContext
from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.runs.events import RunEventRecorder
from opsmesh.orchestration.runs.models import AgentRun


def record_tool_cancellation(session: Session, context: AgentExecutionContext) -> None:
    """Persist cancellation evidence for the current run.

    Temporal owns cancellation of the in-flight Activity. There is no separate
    MCP job, queue record, or polling state to cancel here.
    """
    run = session.scalar(
        select(AgentRun).where(
            AgentRun.workspace_id == context.workspace_id,
            AgentRun.id == context.run_id,
        )
    )
    if run is None:
        raise ValueError("Cancellation run is outside its workspace")
    metadata: dict[str, object] = {
        "execution_control": "temporal_activity_cancellation",
        "workspace_id": str(context.workspace_id),
        "run_id": str(context.run_id),
    }
    RunEventRecorder(session).append_event(
        run,
        "tool.cancellation_requested",
        "Temporal cancelled the active tool Activity",
        metadata,
    )
    AuditService(session).record_system_action(
        workspace_id=context.workspace_id,
        action="tool.cancellation_requested",
        target_type="agent_run",
        target_id=context.run_id,
        metadata=metadata,
    )


__all__ = ["record_tool_cancellation"]
