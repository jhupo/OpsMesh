from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.governance.audit.verification import AuditVerificationService
from opsmesh.identity.auth.dependencies import get_current_user
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.platform.operations.diagnostics import PlatformExecutionDiagnostics
from opsmesh.platform.operations.history import PlatformHistoryService
from opsmesh.platform.operations.schemas import (
    AdminOperationResponse,
    AdminOperationSubmitRequest,
    PlatformExecutionSummary,
    PlatformMetricResponse,
    WorkspaceAuditStatus,
)
from opsmesh.platform.operations.service import AdminOperationService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.http.responses import page_response
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.management.models import Workspace

router = APIRouter(prefix="/operations", dependencies=[Depends(require_platform_admin)])
workspace_router = APIRouter(
    prefix="/workspaces/{workspace_id}/operations", dependencies=[Depends(require_platform_admin)]
)


@router.get("/summary", response_model=PlatformExecutionSummary)
def summary(
    window_seconds: int = Query(default=86400, ge=60, le=2592000),
    session: Session = Depends(get_db_session),
) -> PlatformExecutionSummary:
    return PlatformExecutionDiagnostics(session).summary(window_seconds=window_seconds)


@router.get("/history", response_model=PageResponse[PlatformMetricResponse])
def history(
    start_at: datetime,
    end_at: datetime,
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[PlatformMetricResponse]:
    rows, total = PlatformHistoryService(session).history(page, start_at=start_at, end_at=end_at)
    return page_response(rows, total, page, PlatformMetricResponse)


@router.get("/requests", response_model=PageResponse[AdminOperationResponse])
def requests(
    workspace_id: UUID | None = Query(default=None),
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminOperationResponse]:
    rows, total = AdminOperationService(session).list(page, workspace_id=workspace_id)
    return page_response(rows, total, page, AdminOperationResponse)


@router.get("/requests/{request_id}", response_model=AdminOperationResponse)
def request(request_id: UUID, session: Session = Depends(get_db_session)) -> AdminOperationResponse:
    return AdminOperationResponse.model_validate(AdminOperationService(session).get(request_id))


@router.get("/audit-integrity", response_model=PageResponse[WorkspaceAuditStatus])
def audit_status(
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[WorkspaceAuditStatus]:
    total = session.scalar(select(func.count()).select_from(Workspace)) or 0
    ids = session.scalars(
        select(Workspace.id).order_by(Workspace.id).limit(page.limit).offset(page.offset)
    )
    service = AuditVerificationService(session)
    return PageResponse(
        items=[
            WorkspaceAuditStatus(
                workspace_id=identifier,
                **service.status(
                    identifier, stale_after_seconds=settings.audit_integrity_stale_after_seconds
                ).model_dump(),
            )
            for identifier in ids
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@workspace_router.post(
    "/audit-integrity/verify", response_model=AdminOperationResponse, status_code=202
)
def verify_audit(
    workspace_id: UUID,
    submission: AdminOperationSubmitRequest,
    actor: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> AdminOperationResponse:
    row = AdminOperationService(session).submit(
        request_id=submission.request_id,
        workspace_id=workspace_id,
        actor=actor,
        operation="verify_audit",
        parameters={"reason": submission.reason},
    )
    session.commit()
    return AdminOperationResponse.model_validate(row)


@workspace_router.post(
    "/runs/{run_id}/cancel", response_model=AdminOperationResponse, status_code=202
)
def cancel_run(
    workspace_id: UUID,
    run_id: UUID,
    submission: AdminOperationSubmitRequest,
    actor: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> AdminOperationResponse:
    row = AdminOperationService(session).submit(
        request_id=submission.request_id,
        workspace_id=workspace_id,
        actor=actor,
        operation="cancel_run",
        parameters={"run_id": str(run_id), "reason": submission.reason},
    )
    session.commit()
    return AdminOperationResponse.model_validate(row)
