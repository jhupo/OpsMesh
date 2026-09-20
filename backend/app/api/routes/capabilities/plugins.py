from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from opsmesh_plugin_sdk.services.storage import StoredValue, StoreWrite
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.dependencies.auth import workspace_dependency
from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.core.config import Settings, get_settings
from backend.app.core.db.errors import DatabaseConflictError
from backend.app.core.db.pagination import page_scalars
from backend.app.core.db.session import get_db_session
from backend.app.core.pagination import PageParams
from backend.app.core.security.secrets import SecretEncryptionService
from backend.app.domains.access.context import WorkspaceContext
from backend.app.domains.access.permissions import WorkspaceAction
from backend.app.domains.capabilities.plugins.contracts import (
    CandidateApproval,
    CandidatePreviewRequest,
    CandidatePreviewResponse,
    CandidateResponse,
    CredentialRequest,
    CredentialResponse,
    DownloadRequest,
    DownloadResponse,
    PluginAction,
    PluginBindingResponse,
    PluginInstallRequest,
    PluginInstallResponse,
    PluginReleaseResponse,
    SourceResponse,
    SourceSettings,
    SourceUpdate,
    TrustKeyCreate,
    TrustKeyResponse,
)
from backend.app.domains.capabilities.plugins.deployments import (
    DeploymentRequest,
    DeploymentView,
    PluginDeploymentService,
)
from backend.app.domains.capabilities.plugins.distribution import PluginDistributionService
from backend.app.domains.capabilities.plugins.models import (
    PluginBinding,
    PluginCandidate,
    PluginDownload,
    PluginInstall,
    PluginRelease,
    PluginSource,
    PluginTrustKey,
)
from backend.app.domains.capabilities.plugins.service import PluginService
from backend.app.domains.capabilities.plugins.services import PluginServices

router = APIRouter(prefix="/workspaces/{workspace_id}/plugins", tags=["plugins"])


@router.put("/{install_id}/deployment", response_model=DeploymentView, status_code=202)
def configure_deployment(
    install_id: UUID,
    request: DeploymentRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> object:
    secrets = SecretEncryptionService(
        secret=settings.credential_encryption_secret,
        key_id=settings.credential_encryption_key_id,
        previous_secrets=settings.credential_encryption_previous_secrets,
    )
    try:
        return PluginDeploymentService(session).configure(
            context.workspace.id, install_id, context.user, request, secrets
        )
    except ValueError as exc:
        raise HTTPException(422, "Invalid plugin deployment configuration") from exc


@router.get("/{install_id}/deployment", response_model=DeploymentView)
def deployment_status(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginDeploymentService(session).get(context.workspace.id, install_id)


@router.post("/{install_id}/deployment/stop", response_model=DeploymentView, status_code=202)
def stop_deployment(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginDeploymentService(session).stop(context.workspace.id, install_id, context.user)


@router.post("/{install_id}/credentials", response_model=CredentialResponse, status_code=201)
def rotate_credential(
    install_id: UUID,
    request: CredentialRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> CredentialResponse:
    record, token = PluginServices(session).issue(
        context.workspace.id,
        install_id,
        context.user,
        request.permissions,
        request.lifetime_hours,
    )
    session.commit()
    return CredentialResponse(
        id=record.id, token=token, expires_at=record.expires_at, permissions=record.permissions
    )


@router.delete("/{install_id}/credentials", status_code=204)
def revoke_credentials(
    install_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> None:
    PluginServices(session).revoke(context.workspace.id, install_id, context.user)
    session.commit()


@router.put("/{install_id}/configuration", response_model=StoredValue)
def configure_plugin(
    install_id: UUID,
    request: StoreWrite,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> StoredValue:
    with distribution_errors():
        value = PluginServices(session).configure(
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


@router.post("/sources", response_model=SourceResponse, status_code=201)
def create_source(
    request: SourceSettings,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).configure(
            context.workspace.id, context.user.user_id, request
        )


@router.put("/sources/{source_id}", response_model=SourceResponse)
def update_source(
    source_id: UUID,
    request: SourceUpdate,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).configure(
            context.workspace.id, context.user.user_id, request, source_id
        )


@router.get("/sources", response_model=PageResponse[SourceResponse])
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


@router.post("/sources/{source_id}/sync", response_model=DownloadResponse, status_code=202)
def sync_source(
    source_id: UUID,
    request: DownloadRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        return PluginDistributionService(session).enqueue(
            context.workspace.id, context.user.user_id, source_id, request.request_key
        )


@router.get("/candidates", response_model=PageResponse[CandidateResponse])
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
    "/candidates/{candidate_id}/download", response_model=DownloadResponse, status_code=202
)
def download_candidate(
    candidate_id: UUID,
    request: DownloadRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    with distribution_errors():
        service = PluginDistributionService(session)
        candidate = service.candidate(context.workspace.id, candidate_id)
        return service.enqueue(
            context.workspace.id,
            context.user.user_id,
            candidate.source_id,
            request.request_key,
            candidate_id,
        )


@router.get("/downloads", response_model=PageResponse[DownloadResponse])
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


@router.get("/downloads/{job_id}", response_model=DownloadResponse)
def download_status(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginDistributionService(session).job(context.workspace.id, job_id)


@router.post("/downloads/{job_id}/retry", response_model=DownloadResponse, status_code=202)
def retry_download(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> object:
    return PluginDistributionService(session).retry(
        context.workspace.id, context.user.user_id, job_id
    )


@router.post("/candidates/{candidate_id}/preview", response_model=CandidatePreviewResponse)
def preview_candidate(
    candidate_id: UUID,
    request: CandidatePreviewRequest,
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
    request: CandidateApproval,
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
