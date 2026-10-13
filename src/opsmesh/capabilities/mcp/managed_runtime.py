"""Managed MCP references backed by the native SDK/Temporal execution boundary."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.shared.errors import NotFoundError, PolicyDeniedError


def require_execution_environment(
    session: Session, workspace_id: UUID, runtime_id: UUID
) -> WorkspaceRuntime:
    """Validate an execution environment; no host or container is created here."""
    runtime = session.scalar(
        select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.id == runtime_id,
            WorkspaceRuntime.status == "active",
        )
    )
    if runtime is None:
        raise NotFoundError("Execution environment not found")
    if not runtime.sandbox_backend_id:
        raise PolicyDeniedError("Managed MCP environment has no native sandbox backend")
    return runtime
