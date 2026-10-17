"""Workspace concurrency and environment capacity APIs.

Reservations themselves are not an HTTP contract: execution claims and
releases them inside its own admission path. These routes expose the
workspace's configured limits and currently held amounts, and let an
administrator replace a limit.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction
from opsmesh.workspaces.capacity import service
from opsmesh.workspaces.capacity.schemas import (
    CapacityKindView,
    CapacityLimitUpdateRequest,
    CapacitySnapshotView,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/capacity", tags=["workspaces"])

WorkspaceIdPath = Annotated[UUID, Path()]
CapacityKindPath = Annotated[str, Path(max_length=32)]


def _snapshot_view(session: DbSession, workspace_id: UUID) -> CapacitySnapshotView:
    kinds = service.capacity_snapshot(session, workspace_id=workspace_id)
    return CapacitySnapshotView(
        workspace_id=str(workspace_id),
        kinds=[
            CapacityKindView(
                kind=item.kind,
                limit_value=item.limit_value,
                reserved_value=item.reserved_value,
            )
            for item in kinds
        ],
    )


@router.get("", response_model=CapacitySnapshotView, operation_id="workspaces.capacity.read")
def read_capacity(
    session: DbSession, user: CurrentUserDep, workspace_id: WorkspaceIdPath
) -> CapacitySnapshotView:
    """Read every capacity kind's limit and held reservations."""
    service.read_capacity(session, actor=user, workspace_id=workspace_id)
    return _snapshot_view(session, workspace_id)


@router.put(
    "/{kind}",
    response_model=CapacitySnapshotView,
    operation_id="workspaces.capacity.update_limit",
)
def update_capacity_limit(
    session: DbSession,
    user: CurrentUserDep,
    workspace_id: WorkspaceIdPath,
    kind: CapacityKindPath,
    payload: CapacityLimitUpdateRequest,
) -> CapacitySnapshotView:
    """Replace one capacity kind's limit; below the held amount is refused."""
    with session_transaction(session):
        service.set_limit(
            session,
            actor=user,
            workspace_id=workspace_id,
            kind=kind,
            limit_value=payload.limit_value,
        )
    return _snapshot_view(session, workspace_id)
