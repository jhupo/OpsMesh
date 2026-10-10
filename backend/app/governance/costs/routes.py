from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.governance.costs.models import WorkspaceCostBudget
from backend.app.governance.costs.pricing import CostPricingService
from backend.app.governance.costs.queries import CostQueryService
from backend.app.governance.costs.schemas import (
    CostSummaryResponse,
    ModelPricingRuleCreateRequest,
    ModelPricingRuleResponse,
    ModelUsageRecordResponse,
    WorkspaceCostBudgetRequest,
    WorkspaceCostBudgetResponse,
)
from backend.app.governance.costs.service import CostAccountingService
from backend.app.identity.auth.dependencies import workspace_dependency
from backend.app.identity.authorization.context import WorkspaceContext
from backend.app.identity.authorization.permissions import WorkspaceAction
from backend.app.shared.db.session import get_db_session
from backend.app.shared.http.pagination import PageResponse, pagination_params
from backend.app.shared.pagination import PageParams
from backend.app.shared.utils import ensure_aware_utc

router = APIRouter(prefix="/workspaces/{workspace_id}/costs", tags=["costs"])


@router.get("/usage", response_model=PageResponse[ModelUsageRecordResponse])
def list_model_usage(
    page: PageParams = Depends(pagination_params),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    provider: str | None = Query(default=None),
    model: str | None = Query(default=None),
    trace_id: str | None = Query(default=None, min_length=32, max_length=32),
    request_id: str | None = Query(default=None, min_length=1, max_length=80),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> PageResponse[ModelUsageRecordResponse]:
    start, end = _time_range(start_at, end_at)
    rows, total = CostQueryService(session).list_usage(
        context.workspace.id,
        start_at=start,
        end_at=end,
        provider=provider,
        model=model,
        limit=page.limit,
        offset=page.offset,
        trace_id=trace_id,
        request_id=request_id,
    )
    return PageResponse(
        items=[ModelUsageRecordResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/summary", response_model=CostSummaryResponse)
def cost_summary(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    currency: str = Query(default="USD", pattern="^[A-Za-z]{3}$"),
    group_by: Literal["provider", "model", "agent", "run", "day"] = Query(default="model"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> CostSummaryResponse:
    start, end = _time_range(start_at, end_at)
    try:
        payload = CostQueryService(session).summary(
            context.workspace.id,
            start_at=start,
            end_at=end,
            currency=currency,
            group_by=group_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CostSummaryResponse.model_validate(payload)


@router.get("/pricing-rules", response_model=list[ModelPricingRuleResponse])
def list_pricing_rules(
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> list[ModelPricingRuleResponse]:
    return [
        ModelPricingRuleResponse.model_validate(rule)
        for rule in CostPricingService(session).list_pricing_rules(context.workspace.id)
    ]


@router.post(
    "/pricing-rules",
    response_model=ModelPricingRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pricing_rule(
    request: ModelPricingRuleCreateRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ModelPricingRuleResponse:
    try:
        rule = CostPricingService(session).create_pricing_rule(
            workspace_id=context.workspace.id,
            actor_user_id=context.user.user_id,
            **request.model_dump(),
        )
        session.commit()
        session.refresh(rule)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pricing rule version already exists",
        ) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ModelPricingRuleResponse.model_validate(rule)


@router.post(
    "/pricing-rules/{pricing_rule_id}/disable",
    response_model=ModelPricingRuleResponse,
)
def disable_pricing_rule(
    pricing_rule_id: UUID,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> ModelPricingRuleResponse:
    try:
        rule = CostPricingService(session).disable_pricing_rule(
            workspace_id=context.workspace.id,
            pricing_rule_id=pricing_rule_id,
            actor_user_id=context.user.user_id,
        )
        session.commit()
        session.refresh(rule)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ModelPricingRuleResponse.model_validate(rule)


@router.get("/budget", response_model=WorkspaceCostBudgetResponse)
def get_cost_budget(
    currency: str = Query(default="USD", pattern="^[A-Za-z]{3}$"),
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.READ)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCostBudgetResponse:
    budget = session.scalar(
        select(WorkspaceCostBudget).where(
            WorkspaceCostBudget.workspace_id == context.workspace.id,
            WorkspaceCostBudget.currency == currency.upper(),
        )
    )
    if budget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cost budget not found")
    return WorkspaceCostBudgetResponse.model_validate(budget)


@router.put("/budget", response_model=WorkspaceCostBudgetResponse)
def put_cost_budget(
    request: WorkspaceCostBudgetRequest,
    context: WorkspaceContext = Depends(workspace_dependency(WorkspaceAction.ADMIN)),
    session: Session = Depends(get_db_session),
) -> WorkspaceCostBudgetResponse:
    try:
        budget = CostAccountingService(session).upsert_budget(
            workspace_id=context.workspace.id,
            actor_user_id=context.user.user_id,
            **request.model_dump(),
        )
        session.commit()
        session.refresh(budget)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WorkspaceCostBudgetResponse.model_validate(budget)


def _time_range(
    start_at: datetime | None,
    end_at: datetime | None,
) -> tuple[datetime, datetime]:
    end = ensure_aware_utc(end_at or datetime.now(UTC))
    start = ensure_aware_utc(start_at or end - timedelta(days=30))
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_at must be earlier than end_at",
        )
    if end - start > timedelta(days=366):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cost query range cannot exceed 366 days",
        )
    return start, end
