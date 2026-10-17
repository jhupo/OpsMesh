"""Governance cost APIs: pricing management, budgets and usage queries.

The authorization surfaces are deliberately split: pricing versions are
platform-level administration, while a workspace budget and its usage
belong to the workspace's own owner or admin. Governance never imports
workspaces: the deciding subject is a placeholder dependency bootstrap
replaces with the assembled identity channel and the injected budget
scope admission. The reservation ledger itself has no write route here —
producers settle through the execution claim path, and uncertain
operations reconcile there with their evidence.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from opsmesh.governance.costs import service
from opsmesh.governance.costs.models import (
    PRICING_SUBJECTS,
    RESERVATION_STATUSES,
    CostBudget,
    CostBudgetReservation,
    CostPricingRuleVersion,
    CostUsageRecord,
)
from opsmesh.governance.costs.pricing import unit_prices_of, unit_prices_of_version, usage_of
from opsmesh.governance.costs.schemas import (
    BudgetUpsertRequest,
    BudgetView,
    PricingVersionPage,
    PricingVersionPublishRequest,
    PricingVersionView,
    ReservationPage,
    ReservationView,
    UsagePage,
    UsageView,
)
from opsmesh.governance.costs.service import AdminSubject, BudgetSubject
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import InvalidArgumentError, NotFoundError
from opsmesh.shared.http.dependencies import get_db_session
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(prefix="/governance/costs", tags=["governance"])

DbSession = Annotated[Session, Depends(get_db_session)]
WorkspaceIdPath = Annotated[UUID, Path()]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def require_admin_subject() -> AdminSubject:
    """Placeholder for the platform subject; bootstrap injects identity."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Pricing administration is not assembled",
    )


def require_budget_subject() -> BudgetSubject:
    """Placeholder for the workspace subject; bootstrap injects the admission."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Budget scope authorization is not assembled",
    )


AdminSubjectDep = Annotated[AdminSubject, Depends(require_admin_subject)]
BudgetSubjectDep = Annotated[BudgetSubject, Depends(require_budget_subject)]


def _pricing_view(version: CostPricingRuleVersion) -> PricingVersionView:
    return PricingVersionView(
        pricing_version_id=version.id,
        subject_kind=version.subject_kind,
        subject_key=version.subject_key,
        version=version.version,
        prices=unit_prices_of_version(version),
        digest=version.digest,
        status=version.status,
        published_by_user_id=version.published_by_user_id,
        created_at=version.created_at,
    )


def _budget_view(budget: CostBudget) -> BudgetView:
    return BudgetView(
        budget_id=budget.id,
        workspace_id=budget.workspace_id,
        currency=budget.currency,
        budget_state=service.budget_state_of(budget),
        limit_amount=budget.limit_amount,
        unlimited=budget.unlimited,
        period=budget.period,
        enforcement=budget.enforcement,
        revision=budget.revision,
        current_window_key=budget.current_window_key,
        window_used=budget.window_used,
        window_reserved=budget.window_reserved,
        unlimited_decided_by_user_id=budget.unlimited_decided_by_user_id,
        unlimited_decided_at=budget.unlimited_decided_at,
        updated_at=budget.updated_at,
    )


def _usage_view(record: CostUsageRecord) -> UsageView:
    return UsageView(
        operation_id=record.operation_id,
        subject_kind=record.subject_kind,
        subject_key=record.subject_key,
        usage=usage_of(dict(record.usage)),
        actual_cost=record.actual_cost,
        overage_amount=record.overage_amount,
        pricing_version_id=record.pricing_rule_version_id,
        recorded_at=record.recorded_at,
    )


def _reservation_view(reservation: CostBudgetReservation) -> ReservationView:
    return ReservationView(
        operation_id=reservation.operation_id,
        workspace_id=reservation.workspace_id,
        currency=reservation.currency,
        budget_state=reservation.budget_state,
        window_key=reservation.window_key,
        subject_kind=reservation.subject_kind,
        subject_key=reservation.subject_key,
        pricing_version_id=reservation.pricing_rule_version_id,
        prices=unit_prices_of(dict(reservation.unit_prices)),
        max_cost=reservation.max_cost,
        amount=reservation.amount,
        status=reservation.status,
        run_ref=reservation.run_ref,
        uncertain_reason=reservation.uncertain_reason,
        settled_amount=reservation.settled_amount,
        overage_amount=reservation.overage_amount,
        created_at=reservation.created_at,
        settled_at=reservation.settled_at,
        released_at=reservation.released_at,
        released_reason=reservation.released_reason,
    )


@router.post(
    "/pricing/versions",
    response_model=PricingVersionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="governance.costs.pricing.publish",
)
def publish_pricing_version(
    payload: PricingVersionPublishRequest,
    session: DbSession,
    admin: AdminSubjectDep,
) -> PricingVersionView:
    """Publish one immutable price sheet for one model or tool subject."""
    with session_transaction(session):
        version = service.publish_pricing_version(
            session,
            actor=admin,
            subject_kind=payload.subject_kind,
            subject_key=payload.subject_key,
            prices=payload.prices,
        )
    return _pricing_view(version)


@router.get(
    "/pricing/versions",
    response_model=PricingVersionPage,
    operation_id="governance.costs.pricing.list",
)
def list_pricing_versions(
    session: DbSession,
    admin: AdminSubjectDep,
    subject_kind: Annotated[str | None, Query(max_length=16)] = None,
    subject_key: Annotated[str | None, Query(max_length=255)] = None,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> PricingVersionPage:
    """List the published price sheets, newest version first per subject."""
    if subject_kind is not None and subject_kind not in PRICING_SUBJECTS:
        raise InvalidArgumentError(
            "The pricing subject kind is unknown",
            details={"subject_kind": subject_kind, "known_kinds": list(PRICING_SUBJECTS)},
        )
    versions, total = service.list_pricing_versions(
        session,
        subject_kind=subject_kind,
        subject_key=subject_key,
        page=PageParams(limit=limit, offset=offset),
    )
    return PricingVersionPage(versions=[_pricing_view(item) for item in versions], total=total)


@router.put(
    "/workspaces/{workspace_id}/budget",
    response_model=BudgetView,
    operation_id="governance.costs.budget.upsert",
)
def upsert_budget(
    payload: BudgetUpsertRequest,
    session: DbSession,
    subject: BudgetSubjectDep,
    workspace_id: WorkspaceIdPath,
) -> BudgetView:
    """Create or change one workspace budget under its expected revision."""
    with session_transaction(session):
        budget = service.upsert_budget(
            session,
            actor=subject,
            workspace_id=workspace_id,
            currency=payload.currency,
            limit_amount=payload.limit_amount,
            unlimited=payload.unlimited,
            period=payload.period,
            enforcement=payload.enforcement,
            expected_version=payload.expected_version,
        )
    return _budget_view(budget)


@router.get(
    "/workspaces/{workspace_id}/budget",
    response_model=BudgetView,
    operation_id="governance.costs.budget.read",
)
def read_budget(
    session: DbSession,
    subject: BudgetSubjectDep,
    workspace_id: WorkspaceIdPath,
    currency: Annotated[str, Query(min_length=1, max_length=8)],
) -> BudgetView:
    """Read one workspace budget; no budget is the unconfigured state."""
    budget = service.read_budget(session, workspace_id=workspace_id, currency=currency)
    if budget is None:
        raise NotFoundError(
            "The workspace cost budget is not configured",
            details={"workspace_id": str(workspace_id), "currency": currency},
        )
    return _budget_view(budget)


@router.get(
    "/workspaces/{workspace_id}/usage",
    response_model=UsagePage,
    operation_id="governance.costs.usage.list",
)
def list_usage(
    session: DbSession,
    subject: BudgetSubjectDep,
    workspace_id: WorkspaceIdPath,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> UsagePage:
    """List one workspace's settled usage, newest first."""
    usages, total = service.list_usage(
        session, workspace_id=workspace_id, page=PageParams(limit=limit, offset=offset)
    )
    return UsagePage(usages=[_usage_view(item) for item in usages], total=total)


@router.get(
    "/workspaces/{workspace_id}/reservations",
    response_model=ReservationPage,
    operation_id="governance.costs.reservations.list",
)
def list_reservations(
    session: DbSession,
    subject: BudgetSubjectDep,
    workspace_id: WorkspaceIdPath,
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> ReservationPage:
    """List one workspace's reservations; the uncertain set is the pending work."""
    if status is not None and status not in RESERVATION_STATUSES:
        raise InvalidArgumentError(
            "The reservation status is unknown",
            details={"status": status, "known_statuses": list(RESERVATION_STATUSES)},
        )
    reservations, total = service.list_reservations(
        session,
        workspace_id=workspace_id,
        status=status,
        page=PageParams(limit=limit, offset=offset),
    )
    return ReservationPage(
        reservations=[_reservation_view(item) for item in reservations], total=total
    )


__all__ = ["require_admin_subject", "require_budget_subject", "router"]
