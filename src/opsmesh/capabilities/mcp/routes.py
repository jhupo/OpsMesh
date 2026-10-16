"""MCP type configuration routes: remote templates and local package imports.

A remote template is created from JSON; a local (stdio) template attaches
its package to the draft. Nothing here connects, starts or discovers:
running an MCP server belongs to execution.runtime.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, status

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.mcp.schemas import McpCreateRequest, McpDraftRequest
from opsmesh.capabilities.models import CAPABILITY_TYPE_MCP
from opsmesh.capabilities.routes import StorageRootDep, _stored_version_view
from opsmesh.capabilities.schemas import CapabilityVersionView
from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction

router = APIRouter(prefix="/capabilities/mcp", tags=["capabilities"])


@router.post(
    "",
    response_model=CapabilityVersionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="capabilities.mcp.create",
)
def create_mcp(
    payload: McpCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Create one MCP definition with its first draft version."""
    with session_transaction(session):
        _, version = lifecycle.create_definition(
            session,
            capability_type=CAPABILITY_TYPE_MCP,
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
    operation_id="capabilities.mcp.update_draft",
)
def update_mcp_draft(
    definition_id: UUID,
    payload: McpDraftRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Replace the current draft configuration of one MCP definition."""
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
    operation_id="capabilities.mcp.package.attach",
)
def attach_mcp_package(
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
    body: Annotated[bytes, Body()],
) -> CapabilityVersionView:
    """Attach a local MCP package to the current draft.

    The package is verified against the draft's declared manifests; the
    launch command it declares is only recorded, never executed here.
    """
    with session_transaction(session):
        version = lifecycle.attach_package(
            session,
            definition_id=definition_id,
            package=body,
            filename=f"mcp-{definition_id}.zip",
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


__all__ = ["router"]
