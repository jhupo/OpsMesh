from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.core.db.errors import DatabaseConflictError
from backend.app.core.db.pagination import page_scalars
from backend.app.core.db.session import get_db_session
from backend.app.core.pagination import PageParams
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.capabilities.plugins.contracts import (
    PluginAction,
    PluginBindingResponse,
    PluginInstallRequest,
    PluginInstallResponse,
    PluginReleaseResponse,
    TrustKeyCreate,
    TrustKeyResponse,
)
from backend.app.domains.capabilities.plugins.models import (
    PluginBinding,
    PluginInstall,
    PluginRelease,
    PluginTrustKey,
)
from backend.app.domains.capabilities.plugins.service import PluginService

router = APIRouter(prefix="/workspaces/{workspace_id}/plugins", tags=["plugins"])


@router.get("", response_model=PageResponse[PluginInstallResponse])
def list_plugins(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> object:
    items, total = page_scalars(
        session,
        select(PluginInstall)
        .where(
            PluginInstall.workspace_id == context.workspace.id,
        )
        .order_by(PluginInstall.created_at.desc(), PluginInstall.id),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/trust-keys", response_model=TrustKeyResponse, status_code=201)
def trust_key(
    request: TrustKeyCreate,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return PluginService(session).trust_key(context.workspace.id, context.user.user_id, request)
    except DatabaseConflictError as exc:
        raise HTTPException(409, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid publisher key") from exc


@router.get("/trust-keys", response_model=PageResponse[TrustKeyResponse])
def list_trust_keys(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    items, total = page_scalars(
        session,
        select(PluginTrustKey)
        .where(
            PluginTrustKey.workspace_id == context.workspace.id,
        )
        .order_by(PluginTrustKey.created_at.desc(), PluginTrustKey.id),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/trust-keys/{key_id}/revoke", response_model=TrustKeyResponse)
def revoke_key(
    key_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginService(session).revoke_key(context.workspace.id, context.user.user_id, key_id)


@router.post("", response_model=PluginInstallResponse, status_code=201)
def install_plugin(
    request: PluginInstallRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return PluginService(session).install(context.workspace.id, context.user.user_id, request)
    except DatabaseConflictError as exc:
        raise HTTPException(409, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid plugin package or resource binding") from exc


@router.post("/{install_id}/actions", response_model=PluginInstallResponse)
def plugin_action(
    install_id: UUID,
    request: PluginAction,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return PluginService(session).action(
            context.workspace.id, context.user.user_id, install_id, request
        )
    except ValueError as exc:
        raise HTTPException(400, "Invalid plugin lifecycle action") from exc


@router.get("/{install_id}/releases", response_model=PageResponse[PluginReleaseResponse])
def releases(
    install_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> object:
    PluginService(session).require(context.workspace.id, install_id)
    items, total = page_scalars(
        session,
        select(PluginRelease)
        .where(
            PluginRelease.workspace_id == context.workspace.id,
            PluginRelease.install_id == install_id,
        )
        .order_by(PluginRelease.created_at.desc(), PluginRelease.id),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{install_id}/bindings", response_model=PageResponse[PluginBindingResponse])
def bindings(
    install_id: UUID,
    version: str | None = Query(default=None, max_length=64),
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> object:
    install = PluginService(session).require(context.workspace.id, install_id)
    items, total = page_scalars(
        session,
        select(PluginBinding)
        .join(
            PluginRelease,
            PluginRelease.id == PluginBinding.release_id,
        )
        .where(
            PluginBinding.workspace_id == context.workspace.id,
            PluginBinding.install_id == install_id,
            PluginRelease.workspace_id == context.workspace.id,
            PluginRelease.version == (version or install.current_version),
        )
        .order_by(PluginBinding.capability_key),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{install_id}/dependencies")
def dependencies(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return {"dependents": PluginService(session).dependencies(context.workspace.id, install_id)}
