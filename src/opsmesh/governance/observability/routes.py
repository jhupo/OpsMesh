from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from opsmesh.governance.audit.schemas import AuditEventResponse
from opsmesh.governance.observability.outcome_schemas import OperationsOutcomesResponse
from opsmesh.governance.observability.outcomes import ExecutionOutcomeService
from opsmesh.governance.observability.overview import (
    WorkspaceOperationsOverviewResponse,
    WorkspaceOperationsOverviewService,
)
from opsmesh.governance.observability.schemas import (
    EvidenceCorrelationResponse,
    RuntimeEventEvidenceResponse,
    SecurityEventResponse,
)
from opsmesh.governance.observability.service import OperationalEvidenceService
from opsmesh.identity.auth.dependencies import workspace_dependency
from opsmesh.identity.authorization.context import WorkspaceContext
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.orchestration.runs.contracts import AgentRunResponse, RunEventResponse
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.http.pagination import PageResponse, pagination_params
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.redis.cache import RedisJsonCache
from opsmesh.shared.redis.dependencies import get_cache_service

router = APIRouter(prefix="/workspaces/{workspace_id}/operations", tags=["observability"])


@router.get("/overview", response_model=WorkspaceOperationsOverviewResponse)
def operations_overview(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
    cache: RedisJsonCache = Depends(get_cache_service),
) -> WorkspaceOperationsOverviewResponse:
    result = cache.get_or_set(
        f"overview:{context.workspace.id}",
        lambda: (
            WorkspaceOperationsOverviewService(session)
            .summarize(context.workspace.id)
            .model_dump(mode="json")
        ),
        ttl_seconds=10,
    )
    return WorkspaceOperationsOverviewResponse.model_validate(result.value)


@router.get("/outcomes", response_model=OperationsOutcomesResponse)
def execution_outcomes(
    window_seconds: int = Query(default=86400, ge=60, le=2592000),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> OperationsOutcomesResponse:
    return ExecutionOutcomeService(session).summarize(
        context.workspace.id, window_seconds=window_seconds
    )


@router.get("/correlation", response_model=EvidenceCorrelationResponse)
def operations_correlation(
    settings: Settings = Depends(get_settings),
    trace_id: str | None = Query(default=None, min_length=32, max_length=32),
    request_id: str | None = Query(default=None, min_length=1, max_length=80),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> EvidenceCorrelationResponse:
    try:
        return OperationalEvidenceService(session).correlation(
            context.workspace.id,
            trace_id=trace_id,
            request_id=request_id,
            api_prefix=settings.api_prefix,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/run-events", response_model=PageResponse[RunEventResponse])
def list_run_events(
    page: PageParams = Depends(pagination_params),
    event_type: str | None = Query(default=None),
    trace_id: str | None = Query(default=None, min_length=32, max_length=32),
    request_id: str | None = Query(default=None, min_length=1, max_length=80),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RunEventResponse]:
    items, total = OperationalEvidenceService(session).list_run_events(
        context.workspace.id,
        page,
        event_type,
        trace_id,
        request_id,
    )
    return PageResponse[RunEventResponse](
        items=[RunEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/runtime-events", response_model=PageResponse[RuntimeEventEvidenceResponse])
def list_runtime_events(
    page: PageParams = Depends(pagination_params),
    runtime_id: UUID | None = Query(default=None),
    event_type: str | None = Query(default=None),
    trace_id: str | None = Query(default=None, min_length=32, max_length=32),
    request_id: str | None = Query(default=None, min_length=1, max_length=80),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[RuntimeEventEvidenceResponse]:
    items, total = OperationalEvidenceService(session).list_runtime_events(
        context.workspace.id,
        page,
        runtime_id,
        event_type,
        trace_id,
        request_id,
    )
    return PageResponse(
        items=[RuntimeEventEvidenceResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/failed-runs", response_model=PageResponse[AgentRunResponse])
def inspect_failed_runs(
    page: PageParams = Depends(pagination_params),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AgentRunResponse]:
    runs, total = OperationalEvidenceService(session).inspect_failed_runs(
        context.workspace.id,
        page,
    )
    return PageResponse[AgentRunResponse](
        items=[AgentRunResponse.model_validate(run) for run in runs],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/audit-events", response_model=PageResponse[AuditEventResponse])
def filter_audit_events(
    page: PageParams = Depends(pagination_params),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    trace_id: str | None = Query(default=None, min_length=32, max_length=32),
    request_id: str | None = Query(default=None, min_length=1, max_length=80),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> PageResponse[AuditEventResponse]:
    items, total = OperationalEvidenceService(session).filter_audit_events(
        context.workspace.id,
        page,
        action,
        target_type,
        trace_id,
        request_id,
    )
    return PageResponse[AuditEventResponse](
        items=[AuditEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/security-events", response_model=PageResponse[SecurityEventResponse])
def filter_security_events(
    page: PageParams = Depends(pagination_params),
    action: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.OPERATE)),
    session: Session = Depends(get_db_session),
) -> PageResponse[SecurityEventResponse]:
    items, total = OperationalEvidenceService(session).filter_security_events(
        context.workspace.id,
        page,
        action,
        severity,
        user_id,
    )
    return PageResponse[SecurityEventResponse](
        items=[SecurityEventResponse.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
