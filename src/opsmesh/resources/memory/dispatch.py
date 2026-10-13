from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from opsmesh.resources.memory.models import WorkspaceMemoryEntry
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution


def stage_memory_embedding(session: Session, entry: WorkspaceMemoryEntry) -> str | None:
    if entry.embedding_status != "pending" or entry.memory_layer not in {"episodic", "semantic"}:
        return None
    session.flush([entry])
    execution_id: UUID = uuid5(
        NAMESPACE_URL,
        f"opsmesh:memory:{entry.workspace_id}:{entry.id}:{entry.embedding_generation}",
    )
    workflow_id = stage_execution(
        session,
        ExecutionRequest(
            execution_id=execution_id,
            workspace_id=entry.workspace_id,
            resource_id=entry.id,
            kind=ExecutionKind.MEMORY_EMBED,
            parameters={"embedding_generation": entry.embedding_generation},
        ),
    )
    entry.embedding_status = "queued"
    return workflow_id
