"""Expert type configuration routes: create a definition and edit its draft.

Publishing, versions and packages are served by the common capability
routes; this router owns the expert-specific configuration contract.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, status

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.experts.schemas import ExpertCreateRequest, ExpertDraftRequest
from opsmesh.capabilities.models import CAPABILITY_TYPE_EXPERT
from opsmesh.capabilities.routes import StorageRootDep, _stored_version_view
from opsmesh.capabilities.schemas import CapabilityVersionView
from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction

router = APIRouter(prefix="/capabilities/experts", tags=["capabilities"])


@router.post(
    "",
    response_model=CapabilityVersionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="capabilities.experts.create",
)
def create_expert(
    payload: ExpertCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Create one expert definition with its first draft version."""
    with session_transaction(session):
        _, version = lifecycle.create_definition(
            session,
            capability_type=CAPABILITY_TYPE_EXPERT,
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
    operation_id="capabilities.experts.update_draft",
)
def update_expert_draft(
    definition_id: UUID,
    payload: ExpertDraftRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Replace the current draft configuration of one expert definition."""
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
    operation_id="capabilities.experts.package.attach",
)
def attach_expert_package(
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
    body: Annotated[bytes, Body()],
) -> CapabilityVersionView:
    """Attach a resource package to the current expert draft.

    The package is verified against the draft's declared manifests and
    stored under its digest; nothing inside it is imported or executed.
    """
    with session_transaction(session):
        version = lifecycle.attach_package(
            session,
            definition_id=definition_id,
            package=body,
            filename=f"expert-{definition_id}.zip",
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


__all__ = ["router"]
