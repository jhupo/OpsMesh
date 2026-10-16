"""Skill import routes: create a definition and import its standard package.

A skill's configuration is derived from its package, so the only mutating
operations are creating the definition and importing the package that
becomes its content.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, status

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.models import CAPABILITY_TYPE_SKILL
from opsmesh.capabilities.routes import StorageRootDep, _stored_version_view
from opsmesh.capabilities.schemas import CapabilityVersionView
from opsmesh.capabilities.skills.schemas import SkillCreateRequest
from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.shared.db.session import session_transaction

router = APIRouter(prefix="/capabilities/skills", tags=["capabilities"])


@router.post(
    "",
    response_model=CapabilityVersionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="capabilities.skills.create",
)
def create_skill(
    payload: SkillCreateRequest,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    """Create one skill definition; its content arrives with the package."""
    with session_transaction(session):
        _, version = lifecycle.create_definition(
            session,
            capability_type=CAPABILITY_TYPE_SKILL,
            key=payload.key,
            name=payload.name,
            description=payload.description,
            visibility=payload.visibility,
            configuration=None,
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


@router.put(
    "/{definition_id}/draft/package",
    response_model=CapabilityVersionView,
    operation_id="capabilities.skills.package.attach",
)
def import_skill_package(
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
    body: Annotated[bytes, Body()],
) -> CapabilityVersionView:
    """Import the standard skill package that becomes the draft content.

    The SKILL.md frontmatter and body become the configuration and every
    other file becomes a declared resource; scripts stay resources and are
    never executed by the import.
    """
    with session_transaction(session):
        version = lifecycle.attach_package(
            session,
            definition_id=definition_id,
            package=body,
            filename=f"skill-{definition_id}.zip",
            actor=AuditActor.from_user(user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


__all__ = ["router"]
