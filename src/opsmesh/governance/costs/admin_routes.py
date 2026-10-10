from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from opsmesh.governance.costs.admin_service import AdminCostService
from opsmesh.governance.costs.schemas import CostGroupResponse, CostTotalsResponse
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.utils import ensure_aware_utc

router = APIRouter(prefix="/costs", dependencies=[Depends(require_platform_admin)])


class AdminCostSummaryResponse(BaseModel):
    start_at: datetime
    end_at: datetime
    workspace_id: UUID | None
    currency: str
    group_by: str
    totals: CostTotalsResponse
    groups: list[CostGroupResponse]
    limit: int
    offset: int
    has_more: bool


@router.get("/summary", response_model=AdminCostSummaryResponse)
def cost_summary(
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    workspace_id: UUID | None = None,
    currency: str = Query(default="USD", pattern="^[A-Za-z]{3}$"),
    group_by: Literal["workspace", "provider", "model", "day"] = "workspace",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> AdminCostSummaryResponse:
    end = ensure_aware_utc(end_at or datetime.now(UTC))
    start = ensure_aware_utc(start_at or end - timedelta(days=30))
    if not timedelta(0) < end - start <= timedelta(days=366):
        raise HTTPException(
            status_code=422, detail="Time range must be positive and at most 366 days"
        )
    return AdminCostSummaryResponse.model_validate(
        AdminCostService(session).summary(
            start_at=start,
            end_at=end,
            currency=currency,
            group_by=group_by,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )
    )
