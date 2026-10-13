from contextlib import closing
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from temporalio.service import RPCError

from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.resources.files.security import content_disposition_attachment
from opsmesh.resources.storage.backends import create_storage
from opsmesh.resources.transfers.archive_reconciliation import ArchiveExportReconciliationService
from opsmesh.resources.transfers.contracts import (
    WorkspaceArchiveExportReconciliation,
    WorkspaceArchiveExportRequest,
    WorkspaceArchiveRestoreDrillRequest,
    WorkspaceArchiveVerificationResponse,
    WorkspaceExportJobResponse,
    WorkspaceRestoreDrillOperationResponse,
    WorkspaceRestoreDrillReconciliation,
)
from opsmesh.resources.transfers.restore_operations import RestoreDrillOperationService
from opsmesh.resources.transfers.service import WorkspaceExportService
from opsmesh.resources.transfers.verification_operations import ArchiveVerificationOperationService
from opsmesh.runtime.temporal.client import connect_temporal
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.errors import DomainError
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams

router = APIRouter()


@router.post("/archive/jobs/{job_id}/reconcile", response_model=WorkspaceExportJobResponse)
async def reconcile_workspace_archive_export_job(
    job_id: UUID,
    request: WorkspaceArchiveExportReconciliation,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceExportJobResponse:
    session.commit()
    try:
        temporal = await connect_temporal(settings)
    except (RPCError, RuntimeError, OSError) as error:
        raise DomainError(
            "Temporal execution state could not be confirmed",
            code="archive_export_temporal_unavailable",
            status_code=503,
        ) from error
    with closing(create_storage(settings)) as storage:
        job = await ArchiveExportReconciliationService(session).reconcile(
            workspace_id=context.workspace.id,
            job_id=job_id,
            user=context.user,
            reason=request.reason,
            storage=storage,
            temporal=temporal,
        )
    return WorkspaceExportJobResponse.model_validate(job)


@router.post(
    "/archive/jobs",
    response_model=WorkspaceExportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_workspace_archive_export_job(
    request: WorkspaceArchiveExportRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceExportJobResponse:
    export_job = WorkspaceExportService(session).create_archive_export_job(
        workspace=context.workspace,
        user_id=context.user.user_id,
        request=request,
    )
    return WorkspaceExportJobResponse.model_validate(export_job)


@router.get("/archive/jobs/{job_id}", response_model=WorkspaceExportJobResponse)
def get_workspace_archive_export_job(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceExportJobResponse:
    export_job = WorkspaceExportService(session).get_export_job(
        workspace_id=context.workspace.id,
        job_id=job_id,
    )
    if export_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    return WorkspaceExportJobResponse.model_validate(export_job)


@router.get("/archive/jobs/{job_id}/download")
def download_workspace_archive_export_job(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        with closing(create_storage(settings)) as storage:
            export_job, content = WorkspaceExportService(session).read_export_job_content(
                workspace_id=context.workspace.id,
                job_id=job_id,
                storage=storage,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=export_job.content_type or "application/zip",
        headers={
            "Content-Disposition": content_disposition_attachment(
                export_job.filename or "workspace-archive.zip"
            )
        },
    )


@router.post(
    "/archive/jobs/{job_id}/verify",
    response_model=WorkspaceArchiveVerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def verify_workspace_archive_export_job(
    job_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceArchiveVerificationResponse:
    operation = ArchiveVerificationOperationService(session).create(
        context.workspace.id, job_id, context.user.user_id
    )
    return WorkspaceArchiveVerificationResponse.model_validate(operation)


@router.get(
    "/archive/verifications/{operation_id}", response_model=WorkspaceArchiveVerificationResponse
)
def get_workspace_archive_verification(
    operation_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceArchiveVerificationResponse:
    return WorkspaceArchiveVerificationResponse.model_validate(
        ArchiveVerificationOperationService(session).get(context.workspace.id, operation_id)
    )


@router.post(
    "/archive/jobs/{job_id}/restore-drill",
    response_model=WorkspaceRestoreDrillOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_workspace_archive_restore_drill(
    job_id: UUID,
    request: WorkspaceArchiveRestoreDrillRequest = Body(
        default_factory=WorkspaceArchiveRestoreDrillRequest
    ),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceRestoreDrillOperationResponse:
    operation = RestoreDrillOperationService(session).create(
        context.workspace.id, job_id, context.user.user_id, request
    )
    return WorkspaceRestoreDrillOperationResponse.model_validate(operation)


@router.get(
    "/archive/restore-drills", response_model=PageResponse[WorkspaceRestoreDrillOperationResponse]
)
def list_workspace_restore_drill_operations(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[WorkspaceRestoreDrillOperationResponse]:
    items, total = RestoreDrillOperationService(session).list(context.workspace.id, page)
    return PageResponse(
        items=[WorkspaceRestoreDrillOperationResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/archive/restore-drills/{operation_id}", response_model=WorkspaceRestoreDrillOperationResponse
)
def get_workspace_restore_drill_operation(
    operation_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceRestoreDrillOperationResponse:
    operation = RestoreDrillOperationService(session).get(context.workspace.id, operation_id)
    return WorkspaceRestoreDrillOperationResponse.model_validate(operation)


@router.post(
    "/archive/restore-drills/{operation_id}/reconcile",
    response_model=WorkspaceRestoreDrillOperationResponse,
)
def reconcile_workspace_restore_drill_operation(
    operation_id: UUID,
    request: WorkspaceRestoreDrillReconciliation,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRestoreDrillOperationResponse:
    with closing(create_storage(settings)) as storage:
        operation = RestoreDrillOperationService(session).reconcile(
            context.workspace.id,
            operation_id,
            context.user,
            request.reason,
            storage,
        )
    return WorkspaceRestoreDrillOperationResponse.model_validate(operation)
