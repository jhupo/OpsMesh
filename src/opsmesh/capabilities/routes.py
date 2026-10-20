"""Common static catalog, draft, version and package API for all five capability types."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.models import (
    CAPABILITY_TYPES,
    SOURCES,
    VISIBILITIES,
    CapabilityDefinition,
    CapabilityVersion,
)
from opsmesh.capabilities.schemas import (
    CapabilityDefinitionPage,
    CapabilityDefinitionView,
    CapabilityVersionView,
    DefinitionInput,
    DraftInput,
    UpdateDefinitionRequest,
    UpdateDefinitionStatusRequest,
    UpdateDefinitionVisibilityRequest,
    version_view,
)
from opsmesh.identity.access import PlatformRole, require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def get_storage_root(request: Request) -> Path:
    root = getattr(request.app.state, "storage_root", None)
    if not isinstance(root, str) or not root:
        raise HTTPException(status_code=503, detail="Capability storage is not assembled")
    return Path(root)


StorageRootDep = Annotated[Path, Depends(get_storage_root)]


def _viewer(user: CurrentUser) -> lifecycle.Viewer:
    return lifecycle.Viewer(
        user_id=user.user_id,
        is_platform_admin=user.has_platform_role(PlatformRole.ADMIN),
    )


def _write_actor(session: Session, definition_id: UUID, user: CurrentUser) -> AuditActor:
    definition = lifecycle.get_definition(
        session, definition_id=definition_id, viewer=_viewer(user)
    )
    if definition.source == "system":
        require_platform_admin(user)
        return AuditActor(actor_type="platform", actor_id=str(user.user_id))
    return AuditActor.from_user(user)


def _definition_view(
    definition: CapabilityDefinition, *, latest_version: int, published: bool
) -> CapabilityDefinitionView:
    return CapabilityDefinitionView(
        definition_id=definition.id,
        type=definition.type,
        key=definition.key,
        name=definition.name,
        description=definition.description,
        visibility=definition.visibility,
        status=definition.status,
        source=definition.source,
        created_by_user_id=definition.created_by_user_id,
        latest_version=latest_version,
        has_published_version=published,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


def _summarized_view(
    session: Session, definition: CapabilityDefinition, viewer: lifecycle.Viewer
) -> CapabilityDefinitionView:
    if definition.status == "deleted":
        return _definition_view(definition, latest_version=0, published=False)
    versions = lifecycle.list_versions(session, definition_id=definition.id, viewer=viewer)
    return _definition_view(
        definition,
        latest_version=max((version.version for version in versions), default=0),
        published=any(version.status == "published" for version in versions),
    )


def _stored_version_view(session: Session, version: CapabilityVersion) -> CapabilityVersionView:
    return version_view(
        version,
        package_filename=lifecycle.version_package_filename(session, version_id=version.id),
    )


@router.get(
    "/definitions",
    response_model=CapabilityDefinitionPage,
    operation_id="capabilities.definitions.list",
)
def list_definitions(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    type_filter: Annotated[str | None, Query(alias="type", max_length=20)] = None,
    visibility_filter: Annotated[str | None, Query(alias="visibility", max_length=16)] = None,
    source_filter: Annotated[str | None, Query(alias="source", max_length=16)] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=16)] = None,
    key_query: Annotated[str | None, Query(max_length=100)] = None,
    owned_only: bool = False,
) -> CapabilityDefinitionPage:
    for name, value, allowed in (
        ("type", type_filter, CAPABILITY_TYPES),
        ("visibility", visibility_filter, VISIBILITIES),
        ("source", source_filter, SOURCES),
    ):
        if value is not None and value not in allowed:
            raise InvalidArgumentError(f"The {name} filter is unknown")
    definitions, total = lifecycle.list_definitions(
        session,
        page=PageParams(limit=limit, offset=offset),
        viewer=_viewer(user),
        capability_type=type_filter,
        visibility=visibility_filter,
        source=source_filter,
        status=status_filter,
        key_query=key_query,
        owned_only=owned_only,
    )
    summaries = lifecycle.definition_summaries(
        session, definitions=definitions, viewer=_viewer(user)
    )
    return CapabilityDefinitionPage(
        items=[
            _definition_view(
                definition,
                latest_version=summaries.get(definition.id, (0, False))[0],
                published=summaries.get(definition.id, (0, False))[1],
            )
            for definition in definitions
        ],
        total=total,
    )


@router.post(
    "/definitions",
    response_model=CapabilityVersionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="capabilities.definitions.create",
)
def create_definition(
    payload: DefinitionInput,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    actor = AuditActor.from_user(user)
    if payload.source == "system":
        require_platform_admin(user)
        actor = AuditActor(actor_type="platform", actor_id=str(user.user_id))
    configuration = payload.declaration.configuration
    with session_transaction(session):
        _, version = lifecycle.create_definition(
            session,
            capability_type=payload.declaration.type,
            key=payload.key,
            name=payload.name,
            description=payload.description,
            visibility=payload.visibility,
            source=payload.source,
            configuration=configuration.model_dump(mode="json") if configuration else None,
            resource_manifest=payload.resource_manifest,
            dependencies=payload.dependencies,
            flow_templates=payload.flow_templates,
            actor=actor,
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


@router.get(
    "/definitions/{definition_id}",
    response_model=CapabilityDefinitionView,
    operation_id="capabilities.definitions.read",
)
def read_definition(
    definition_id: UUID, session: DbSession, user: CurrentUserDep
) -> CapabilityDefinitionView:
    definition = lifecycle.get_definition(
        session, definition_id=definition_id, viewer=_viewer(user)
    )
    return _summarized_view(session, definition, _viewer(user))


@router.patch(
    "/definitions/{definition_id}",
    response_model=CapabilityDefinitionView,
    operation_id="capabilities.definitions.update",
)
def update_definition(
    definition_id: UUID, payload: UpdateDefinitionRequest, session: DbSession, user: CurrentUserDep
) -> CapabilityDefinitionView:
    with session_transaction(session):
        definition = lifecycle.update_definition_metadata(
            session,
            definition_id=definition_id,
            name=payload.name,
            description=payload.description,
            actor=_write_actor(session, definition_id, user),
        )
    return _summarized_view(session, definition, _viewer(user))


@router.post(
    "/definitions/{definition_id}/visibility",
    response_model=CapabilityDefinitionView,
    operation_id="capabilities.definitions.update_visibility",
)
def update_definition_visibility(
    definition_id: UUID,
    payload: UpdateDefinitionVisibilityRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> CapabilityDefinitionView:
    with session_transaction(session):
        definition = lifecycle.set_visibility(
            session,
            definition_id=definition_id,
            visibility=payload.visibility,
            actor=_write_actor(session, definition_id, user),
        )
    return _summarized_view(session, definition, _viewer(user))


@router.post(
    "/definitions/{definition_id}/status",
    response_model=CapabilityDefinitionView,
    operation_id="capabilities.definitions.update_status",
)
def update_definition_status(
    definition_id: UUID,
    payload: UpdateDefinitionStatusRequest,
    session: DbSession,
    user: CurrentUserDep,
) -> CapabilityDefinitionView:
    with session_transaction(session):
        definition = lifecycle.set_definition_status(
            session,
            definition_id=definition_id,
            status=payload.status,
            actor=_write_actor(session, definition_id, user),
            actor_is_platform_admin=user.has_platform_role(PlatformRole.ADMIN),
        )
    return _summarized_view(session, definition, _viewer(user))


@router.put(
    "/definitions/{definition_id}/draft",
    response_model=CapabilityVersionView,
    operation_id="capabilities.definitions.update_draft",
)
def update_draft(
    definition_id: UUID,
    payload: DraftInput,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    configuration = payload.declaration.configuration
    with session_transaction(session):
        version = lifecycle.update_draft(
            session,
            definition_id=definition_id,
            capability_type=payload.declaration.type,
            configuration=configuration.model_dump(mode="json") if configuration else None,
            resource_manifest=payload.resource_manifest,
            dependencies=payload.dependencies,
            flow_templates=payload.flow_templates,
            actor=_write_actor(session, definition_id, user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


@router.put(
    "/definitions/{definition_id}/draft/package",
    response_model=CapabilityVersionView,
    operation_id="capabilities.definitions.package.attach",
)
def attach_package(
    definition_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
    body: Annotated[bytes, Body(media_type="application/octet-stream")],
    filename: Annotated[str, Query(min_length=1, max_length=260)] = "package.zip",
) -> CapabilityVersionView:
    with session_transaction(session):
        version = lifecycle.attach_package(
            session,
            definition_id=definition_id,
            package=body,
            filename=filename,
            actor=_write_actor(session, definition_id, user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


@router.get(
    "/definitions/{definition_id}/versions",
    response_model=list[CapabilityVersionView],
    operation_id="capabilities.versions.list",
)
def list_versions(
    definition_id: UUID, session: DbSession, user: CurrentUserDep
) -> list[CapabilityVersionView]:
    versions = lifecycle.list_versions(session, definition_id=definition_id, viewer=_viewer(user))
    return [_stored_version_view(session, version) for version in versions]


@router.get(
    "/definitions/{definition_id}/versions/{version_number}",
    response_model=CapabilityVersionView,
    operation_id="capabilities.versions.read",
)
def read_version(
    definition_id: UUID, version_number: int, session: DbSession, user: CurrentUserDep
) -> CapabilityVersionView:
    version = lifecycle.get_version(
        session,
        definition_id=definition_id,
        version_number=version_number,
        viewer=_viewer(user),
    )
    return _stored_version_view(session, version)


@router.post(
    "/definitions/{definition_id}/versions/{version_number}/publish",
    response_model=CapabilityVersionView,
    operation_id="capabilities.versions.publish",
)
def publish_version(
    definition_id: UUID,
    version_number: int,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> CapabilityVersionView:
    with session_transaction(session):
        version = lifecycle.publish_version(
            session,
            definition_id=definition_id,
            version_number=version_number,
            actor=_write_actor(session, definition_id, user),
            storage_root=storage_root,
        )
    return _stored_version_view(session, version)


@router.get(
    "/definitions/{definition_id}/versions/{version_number}/package",
    operation_id="capabilities.versions.package.read",
)
def read_version_package(
    definition_id: UUID,
    version_number: int,
    session: DbSession,
    user: CurrentUserDep,
    storage_root: StorageRootDep,
) -> Response:
    version = lifecycle.get_version(
        session,
        definition_id=definition_id,
        version_number=version_number,
        viewer=_viewer(user),
    )
    stored, payload = lifecycle.read_version_package(
        session,
        version_id=version.id,
        storage_root=storage_root,
    )
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{stored.filename}"',
            "Cache-Control": "no-store",
        },
    )
