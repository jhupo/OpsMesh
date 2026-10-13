"""Map persisted run definitions to their single durable execution identity."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest


def run_execution_reference(
    session: Session,
    run: AgentRun,
    actor_user_id: UUID | None = None,
) -> ExecutionRequest:
    node_type = None
    if run.task_step_id is not None:
        snapshot = run.input.get("authorization_snapshot")
        definition = snapshot.get("node_definition") if isinstance(snapshot, dict) else None
        if not isinstance(definition, dict):
            raise ValueError("Run has no frozen workflow node definition")
        node_type = definition.get("node_type")
    return ExecutionRequest(
        execution_id=run.id,
        workspace_id=run.workspace_id,
        resource_id=run.id,
        kind=ExecutionKind.ORCHESTRATION
        if node_type
        in {
            "tool",
            "mcp",
            "approval",
            "subworkflow",
        }
        else ExecutionKind.AGENT,
        requested_by_user_id=actor_user_id,
    )
