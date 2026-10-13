"""Order short execution-tree transactions before taking individual row locks."""

from hashlib import blake2b
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.subworkflow_models import SubworkflowInvocation


def lock_task_execution_tree(session: Session, workspace_id: UUID, task_id: UUID) -> None:
    root = task_id
    visited: set[UUID] = set()
    while root not in visited:
        visited.add(root)
        parent = session.scalar(
            select(SubworkflowInvocation.parent_task_id).where(
                SubworkflowInvocation.workspace_id == workspace_id,
                SubworkflowInvocation.child_task_id == root,
            )
        )
        if parent is None:
            break
        root = parent
    else:
        raise ValueError("Subworkflow execution graph contains a cycle")
    key = int.from_bytes(
        blake2b(workspace_id.bytes + root.bytes, digest_size=8).digest(), signed=True
    )
    session.execute(select(func.pg_advisory_xact_lock(key)))


def lock_run_execution_tree(session: Session, workspace_id: UUID, run_id: UUID) -> None:
    task_id = session.scalar(
        select(AgentRun.task_id).where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.id == run_id,
        )
    )
    lock_task_execution_tree(session, workspace_id, task_id or run_id)
