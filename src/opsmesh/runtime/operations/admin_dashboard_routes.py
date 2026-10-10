from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from opsmesh.governance.audit.chain_schemas import AuditIntegrityCheckResponse
from opsmesh.identity.authorization.admin_actor import require_admin_actor
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.orchestration.runs.contracts import AgentRunResponse
from opsmesh.runtime.operations.admin_queries import AdminDiagnosticsService
from opsmesh.runtime.operations.admin_requests import AdminOperationService
from opsmesh.runtime.operations.contracts.queue import StaleRunRecoverStatus
from opsmesh.runtime.operations.contracts.scheduler import OperationsSchedulerResponse
from opsmesh.runtime.operations.history import PlatformHistoryService
from opsmesh.runtime.operations.scheduler import SchedulerBacklogService
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.contracts import ORMModel
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.utils import ensure_aware_utc

router = APIRouter(dependencies=[Depends(require_platform_admin)])


class AdminOperationResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    actor_user_id: UUID
    operation: str
    status: str
    result: dict[str, object]
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=240)


class RecoveryRequest(OperationRequest):
    stale_after_seconds: int = Field(default=900, ge=60, le=86400)
    statuses: list[StaleRunRecoverStatus] = Field(
        default=["queued", "running", "waiting_runtime"],
        min_length=1,
        max_length=3,
    )
    limit: int = Field(default=100, ge=1, le=100)


class HistoryResponse(ORMModel):
    id: UUID
    created_at: datetime
    values: dict[str, object]


class IntegrityResponse(BaseModel):
    workspace_id: UUID
    workspace_name: str
    status: Literal["missing", "invalid", "stale", "valid"]
    stale: bool
    latest: AuditIntegrityCheckResponse | None


@router.get("/operations/runs", response_model=PageResponse[AgentRunResponse])
def diagnostic_runs(
    kind: Literal["failed", "stale"] = "failed",
    workspace_id: UUID | None = None,
    stale_after_seconds: int = Query(default=900, ge=60, le=86400),
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentRunResponse]:
    rows, total = AdminDiagnosticsService(session).runs(
        page,
        workspace_id=workspace_id,
        kind=kind,
        stale_after_seconds=stale_after_seconds,
    )
    return PageResponse(
        items=[AgentRunResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/workspaces/{workspace_id}/operations/scheduler", response_model=OperationsSchedulerResponse
)
def scheduler(
    workspace_id: UUID, session: Session = Depends(get_db_session)
) -> OperationsSchedulerResponse:
    AdminDiagnosticsService(session).workspace(workspace_id)
    return SchedulerBacklogService(session).scheduler_payload(workspace_id)


@router.post(
    "/workspaces/{workspace_id}/operations/stale-runs/recover",
    response_model=AdminOperationResponse,
    status_code=202,
)
def recover(
    workspace_id: UUID,
    body: RecoveryRequest,
    actor: AuthenticatedUser = Depends(require_admin_actor),
    session: Session = Depends(get_db_session),
) -> AdminOperationResponse:
    row = AdminOperationService(session).submit(
        request_id=body.request_id,
        workspace_id=workspace_id,
        actor=actor,
        operation="recover_stale_runs",
        parameters=body.model_dump(mode="json", exclude={"request_id"}),
    )
    return AdminOperationResponse.model_validate(row)


@router.get("/operations/requests", response_model=PageResponse[AdminOperationResponse])
def operation_requests(
    workspace_id: UUID | None = None,
    status: Literal["pending", "running", "completed", "failed"] | None = None,
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminOperationResponse]:
    rows, total = AdminDiagnosticsService(session).requests(
        page, workspace_id=workspace_id, status=status
    )
    return PageResponse(
        items=[AdminOperationResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/operations/requests/{request_id}", response_model=AdminOperationResponse)
def operation(
    request_id: UUID, session: Session = Depends(get_db_session)
) -> AdminOperationResponse:
    return AdminOperationResponse.model_validate(
        AdminDiagnosticsService(session).request(request_id)
    )


@router.get("/operations/audit-integrity", response_model=PageResponse[IntegrityResponse])
def integrity(
    workspace_id: UUID | None = None,
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> PageResponse[IntegrityResponse]:
    rows, total = AdminDiagnosticsService(session).integrity(
        page,
        workspace_id=workspace_id,
        stale_after_seconds=settings.audit_integrity_stale_after_seconds,
    )
    return PageResponse(
        items=[IntegrityResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/workspaces/{workspace_id}/operations/audit-integrity/verify",
    response_model=AdminOperationResponse,
    status_code=202,
)
def verify(
    workspace_id: UUID,
    body: OperationRequest,
    actor: AuthenticatedUser = Depends(require_admin_actor),
    session: Session = Depends(get_db_session),
) -> AdminOperationResponse:
    return AdminOperationResponse.model_validate(
        AdminOperationService(session).submit(
            request_id=body.request_id,
            workspace_id=workspace_id,
            actor=actor,
            operation="verify_audit",
            parameters={"reason": body.reason},
        )
    )


@router.get("/operations/history", response_model=PageResponse[HistoryResponse])
def history(
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    page: PageParams = Depends(pagination_params),
    session: Session = Depends(get_db_session),
) -> PageResponse[HistoryResponse]:
    end = ensure_aware_utc(end_at or datetime.now(UTC))
    start = ensure_aware_utc(start_at or end - timedelta(days=1))
    if not timedelta(0) < end - start <= timedelta(days=366):
        raise HTTPException(
            status_code=422, detail="Time range must be positive and at most 366 days"
        )
    rows, total = PlatformHistoryService(session).history(page, start_at=start, end_at=end)
    return PageResponse(
        items=[HistoryResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
