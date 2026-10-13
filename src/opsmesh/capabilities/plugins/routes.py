from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from opsmesh_plugin_sdk.services.storage import StoredValue, StoreWrite
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService
from opsmesh.capabilities.plugins.configuration import PluginConfigurationService
from opsmesh.capabilities.plugins.contracts import (
    PluginActionRequest,
    PluginBindingReauthorizeRequest,
    PluginBindingRequest,
    PluginBindingResponse,
    PluginCandidateInstallRequest,
    PluginCandidatePreviewRequest,
    PluginCandidatePreviewResponse,
    PluginCandidateResponse,
    PluginCredentialResponse,
    PluginCredentialRotateRequest,
    PluginDownloadRequest,
    PluginDownloadResponse,
    PluginInstallRequest,
    PluginInstallResponse,
    PluginReleaseResponse,
    PluginSourceResponse,
    PluginSourceSettings,
    PluginSourceUpdateRequest,
    PluginTrustKeyCreateRequest,
    PluginTrustKeyResponse,
)
from opsmesh.capabilities.plugins.distribution import PluginDistributionService
from opsmesh.capabilities.plugins.lifecycle import PluginLifecycleService
from opsmesh.capabilities.plugins.models import (
    PluginBinding,
    PluginCandidate,
    PluginDownload,
    PluginInstallation,
    PluginRelease,
    PluginSource,
    PluginTrustKey,
)
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.shared.db.errors import DatabaseConflictError
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter(prefix="/workspaces/{workspace_id}/plugins", tags=["plugins"])


@router.post("/{install_id}/credentials", response_model=PluginCredentialResponse, status_code=201)
def rotate_credential(
    install_id: UUID,
    request: PluginCredentialRotateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> PluginCredentialResponse:
    record, token = PluginAuthenticationService(session).rotate_credential(
        context.workspace.id,
        install_id,
        context.user,
        request.permissions,
        request.lifetime_hours,
    )
    session.commit()
    return PluginCredentialResponse(
        id=record.id, token=token, expires_at=record.expires_at, permissions=record.permissions
    )


@router.delete("/{install_id}/credentials", status_code=204)
def revoke_credentials(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> None:
    PluginAuthenticationService(session).revoke(context.workspace.id, install_id, context.user)
    session.commit()


@router.put("/{install_id}/configuration", response_model=StoredValue)
def configure_plugin(
    install_id: UUID,
    request: StoreWrite,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> StoredValue:
    with distribution_errors():
        value = PluginConfigurationService(session).configure(
            context.workspace.id, install_id, context.user, request
        )
        session.commit()
        return value


@contextmanager
def distribution_errors() -> Iterator[None]:
    try:
        yield
    except DatabaseConflictError as exc:
        raise HTTPException(409, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid plugin distribution request") from exc


@router.post("/sources", response_model=PluginSourceResponse, status_code=201)
def create_source(
    request: PluginSourceSettings,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).configure(
            context.workspace.id, context.user.user_id, request
        )


@router.put("/sources/{source_id}", response_model=PluginSourceResponse)
def update_source(
    source_id: UUID,
    request: PluginSourceUpdateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).configure(
            context.workspace.id, context.user.user_id, request, source_id
        )


@router.get("/sources", response_model=PageResponse[PluginSourceResponse])
def sources(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    items, total = page_scalars(
        session,
        select(PluginSource)
        .where(
            PluginSource.workspace_id == context.workspace.id,
        )
        .order_by(PluginSource.created_at.desc(), PluginSource.id),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/sources/{source_id}/sync", response_model=PluginDownloadResponse, status_code=202)
def sync_source(
    source_id: UUID,
    request: PluginDownloadRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).enqueue(
            context.workspace.id, context.user, source_id, request.request_key
        )


@router.get("/candidates", response_model=PageResponse[PluginCandidateResponse])
def candidates(
    source_id: UUID | None = None,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    query = select(PluginCandidate).where(PluginCandidate.workspace_id == context.workspace.id)
    if source_id is not None:
        PluginDistributionService(session).source(context.workspace.id, source_id)
        query = query.where(PluginCandidate.source_id == source_id)
    items, total = page_scalars(
        session, query.order_by(PluginCandidate.created_at.desc(), PluginCandidate.id), page
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/candidates/{candidate_id}/download", response_model=PluginDownloadResponse, status_code=202
)
def download_candidate(
    candidate_id: UUID,
    request: PluginDownloadRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        service = PluginDistributionService(session)
        candidate = service.candidate(context.workspace.id, candidate_id)
        return service.enqueue(
            context.workspace.id,
            context.user,
            candidate.source_id,
            request.request_key,
            candidate_id,
        )


@router.get("/downloads", response_model=PageResponse[PluginDownloadResponse])
def downloads(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    items, total = page_scalars(
        session,
        select(PluginDownload)
        .where(
            PluginDownload.workspace_id == context.workspace.id,
        )
        .order_by(PluginDownload.created_at.desc(), PluginDownload.id),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/downloads/{job_id}", response_model=PluginDownloadResponse)
def download_status(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginDistributionService(session).job(context.workspace.id, job_id)


@router.post("/downloads/{job_id}/retry", response_model=PluginDownloadResponse, status_code=202)
def retry_download(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginDistributionService(session).retry(context.workspace.id, context.user, job_id)


@router.post("/candidates/{candidate_id}/preview", response_model=PluginCandidatePreviewResponse)
def preview_candidate(
    candidate_id: UUID,
    request: PluginCandidatePreviewRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).preview(
            context.workspace.id, context.user.user_id, candidate_id, request
        )


@router.post(
    "/candidates/{candidate_id}/install", response_model=PluginInstallResponse, status_code=201
)
def install_candidate(
    candidate_id: UUID,
    request: PluginCandidateInstallRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).install(
            context.workspace.id, context.user.user_id, candidate_id, request
        )


@router.get("", response_model=PageResponse[PluginInstallResponse])
def list_plugins(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> object:
    items, total = page_scalars(
        session,
        select(PluginInstallation)
        .where(
            PluginInstallation.workspace_id == context.workspace.id,
        )
        .order_by(PluginInstallation.created_at.desc(), PluginInstallation.id),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/trust-keys", response_model=PluginTrustKeyResponse, status_code=201)
def trust_key(
    request: PluginTrustKeyCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return PluginLifecycleService(session).trust_key(
            context.workspace.id, context.user.user_id, request
        )
    except DatabaseConflictError as exc:
        raise HTTPException(409, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid publisher key") from exc


@router.get("/trust-keys", response_model=PageResponse[PluginTrustKeyResponse])
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


@router.post("/trust-keys/{key_id}/revoke", response_model=PluginTrustKeyResponse)
def revoke_key(
    key_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginLifecycleService(session).revoke_key(
        context.workspace.id, context.user.user_id, key_id
    )


@router.post("", response_model=PluginInstallResponse, status_code=201)
def install_plugin(
    request: PluginInstallRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return PluginLifecycleService(session).install(
            context.workspace.id, context.user.user_id, request
        )
    except DatabaseConflictError as exc:
        raise HTTPException(409, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid plugin package or resource binding") from exc


@router.post("/{install_id}/actions", response_model=PluginInstallResponse)
def plugin_action(
    install_id: UUID,
    request: PluginActionRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    try:
        return PluginLifecycleService(session).action(
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
    install = PluginLifecycleService(session).require(context.workspace.id, install_id)
    items, total = page_scalars(
        session,
        select(PluginRelease)
        .where(
            PluginRelease.workspace_id == context.workspace.id,
            PluginRelease.plugin_key == install.plugin_key,
        )
        .order_by(PluginRelease.created_at.desc(), PluginRelease.id),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{install_id}/bindings", response_model=PageResponse[PluginBindingResponse])
def bindings(
    install_id: UUID,
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> object:
    PluginLifecycleService(session).require(context.workspace.id, install_id)
    items, total = page_scalars(
        session,
        select(PluginBinding)
        .where(
            PluginBinding.workspace_id == context.workspace.id,
            PluginBinding.install_id == install_id,
        )
        .order_by(PluginBinding.capability_key),
        page,
    )
    return PageResponse(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/{install_id}/bindings", response_model=PluginBindingResponse, status_code=201)
def bind_plugin_resource(
    install_id: UUID,
    request: PluginBindingRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginLifecycleService(session).bind(
            context.workspace.id, context.user.user_id, install_id, request
        )


@router.post(
    "/{install_id}/bindings/{binding_id}/reauthorize", response_model=PluginBindingResponse
)
def reauthorize_plugin_resource(
    install_id: UUID,
    binding_id: UUID,
    request: PluginBindingReauthorizeRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginLifecycleService(session).reauthorize(
            context.workspace.id, context.user.user_id, install_id, binding_id, request
        )


@router.get("/{install_id}/dependencies")
def dependencies(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return {
        "dependents": PluginLifecycleService(session).dependencies(context.workspace.id, install_id)
    }


@router.delete("/{install_id}/bindings/{binding_id}", status_code=204)
def unbind_plugin_resource(
    install_id: UUID,
    binding_id: UUID,
    expected_generation: int = Query(ge=1),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> None:
    PluginLifecycleService(session).unbind(
        context.workspace.id, context.user.user_id, install_id, binding_id, expected_generation
    )
