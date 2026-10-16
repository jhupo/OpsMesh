"""Tool type configuration routes: SDK exports, scripts and package imports.

A tool declares its execution mode, entrypoint and operations. The
package import verifies the entrypoint statically; no module is imported
and no script is run by these routes.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, status

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.models import CAPABILITY_TYPE_TOOL
from opsmesh.capabilities.routes import StorageRootDep, _stored_version_view
from opsmesh.capabilities.schemas import CapabilityVersionView
from opsmesh.capabilities.tools.schemas import ToolCreateRequest, ToolDraftRequest
from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction

router = APIRouter(prefix="/capabilities/tools", tags=["capabilities"])


@router.post(
    "",
    response_model=CapabilityVersionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="capabilities.tools.create",
)
def create_tool(
    payload: ToolCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Create one tool definition with its first draft version."""
    with session_transaction(session):
        _, version = lifecycle.create_definition(
            session,
            capability_type=CAPABILITY_TYPE_TOOL,
            key=payload.key,
            name=payload.name,
            description=payload.description,
            visibility=payload.visibility,
            configuration=payload.configuration.model_dump(mode="json"),
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


@router.put(
    "/{definition_id}/draft",
    response_model=CapabilityVersionView,
    operation_id="capabilities.tools.update_draft",
)
def update_tool_draft(
    definition_id: UUID,
    payload: ToolDraftRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Replace the current draft configuration of one tool definition."""
    with session_transaction(session):
        version = lifecycle.update_draft(
            session,
            definition_id=definition_id,
            configuration=payload.configuration.model_dump(mode="json"),
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


@router.put(
    "/{definition_id}/draft/package",
    response_model=CapabilityVersionView,
    operation_id="capabilities.tools.package.attach",
)
def attach_tool_package(
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
    body: Annotated[bytes, Body()],
) -> CapabilityVersionView:
    """Attach a tool package to the current draft.

    A script entrypoint must be a file the package declares and carries;
    an SDK export entrypoint is checked as an identifier path. Nothing is
    imported or executed.
    """
    with session_transaction(session):
        version = lifecycle.attach_package(
            session,
            definition_id=definition_id,
            package=body,
            filename=f"tool-{definition_id}.zip",
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


__all__ = ["router"]
