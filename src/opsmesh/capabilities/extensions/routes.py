"""Extension type configuration routes: declared exports and packages.

An extension declares the operations, hooks and events its package
installs with. The package import verifies the declarations against the
package statically; no module is imported and no export is registered by
these routes.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, status

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.extensions.schemas import (
    ExtensionCreateRequest,
    ExtensionDraftRequest,
)
from opsmesh.capabilities.models import CAPABILITY_TYPE_EXTENSION
from opsmesh.capabilities.routes import StorageRootDep, _stored_version_view
from opsmesh.capabilities.schemas import CapabilityVersionView
from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction

router = APIRouter(prefix="/capabilities/extensions", tags=["capabilities"])


@router.post(
    "",
    response_model=CapabilityVersionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="capabilities.extensions.create",
)
def create_extension(
    payload: ExtensionCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Create one extension definition with its first draft version."""
    with session_transaction(session):
        _, version = lifecycle.create_definition(
            session,
            capability_type=CAPABILITY_TYPE_EXTENSION,
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
    operation_id="capabilities.extensions.update_draft",
)
def update_extension_draft(
    definition_id: UUID,
    payload: ExtensionDraftRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Replace the current draft configuration of one extension definition."""
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
    operation_id="capabilities.extensions.package.attach",
)
def attach_extension_package(
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
    body: Annotated[bytes, Body()],
) -> CapabilityVersionView:
    """Attach an extension package to the current draft.

    The declared resources and flow templates must exist in the package
    with their digests. Nothing is imported or executed.
    """
    with session_transaction(session):
        version = lifecycle.attach_package(
            session,
            definition_id=definition_id,
            package=body,
            filename=f"extension-{definition_id}.zip",
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


__all__ = ["router"]
