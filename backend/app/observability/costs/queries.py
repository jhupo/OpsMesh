"""Workspace-scoped cost usage, aggregation, and budget projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.utils import ensure_aware_utc
from backend.app.domains.access.resource_queries import resource_query_scope
from backend.app.domains.access.resources import ResourceAccessDenied
from backend.app.domains.agents.providers.policy import canonical_model_provider
from backend.app.observability.costs.models import ModelUsageRecord, WorkspaceCostBudget
from backend.app.observability.costs.pricing import normalize_currency


@dataclass(frozen=True)
class CostBudgetStatus:
    state: str
    currency: str
    spent: Decimal
    monthly_limit: Decimal | None
    warning_ratio: Decimal | None
    utilization_ratio: Decimal | None
    enforcement: str | None
    unpriced_records: int
    period_start: datetime
    period_end: datetime


class CostQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_usage(
        self,
        workspace_id: UUID,
        *,
        start_at: datetime,
        end_at: datetime,
        provider: str | None,
        model: str | None,
        limit: int,
        offset: int,
        trace_id: str | None = None,
        request_id: str | None = None,
    ) -> tuple[list[ModelUsageRecord], int]:
        filters = [
            ModelUsageRecord.workspace_id == workspace_id,
            ModelUsageRecord.occurred_at >= start_at,
            ModelUsageRecord.occurred_at < end_at,
        ]
        if provider is not None:
            filters.append(ModelUsageRecord.provider == canonical_model_provider(provider))
        if model is not None:
            filters.append(ModelUsageRecord.model == model)
        if trace_id is not None:
            filters.append(ModelUsageRecord.trace_id == trace_id)
        if request_id is not None:
            filters.append(ModelUsageRecord.request_id == request_id)
        total = int(
            self._session.scalar(select(func.count()).select_from(ModelUsageRecord).where(*filters))
            or 0
        )
        rows = self._session.scalars(
            select(ModelUsageRecord)
            .where(*filters)
            .order_by(ModelUsageRecord.occurred_at.desc(), ModelUsageRecord.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return list(rows), total

    def summary(
        self,
        workspace_id: UUID,
        *,
        start_at: datetime,
        end_at: datetime,
        currency: str,
        group_by: str,
    ) -> dict[str, object]:
        normalized_currency = normalize_currency(currency)
        filters = (
            ModelUsageRecord.workspace_id == workspace_id,
            ModelUsageRecord.occurred_at >= start_at,
            ModelUsageRecord.occurred_at < end_at,
            or_(
                ModelUsageRecord.currency == normalized_currency,
                ModelUsageRecord.currency.is_(None),
            ),
        )
        aggregate_columns = _aggregate_columns(normalized_currency)
        totals_row = (
            self._session.execute(select(*aggregate_columns).where(*filters)).mappings().one()
        )
        group_expression = _group_expression(group_by)
        group_rows = self._session.execute(
            select(group_expression.label("key"), *aggregate_columns)
            .where(*filters)
            .group_by(group_expression)
            .order_by(group_expression)
        ).mappings()
        return {
            "start_at": start_at,
            "end_at": end_at,
            "currency": normalized_currency,
            "group_by": group_by,
            "totals": _totals_from_row(totals_row),
            "groups": [{"key": str(row["key"]), **_totals_from_row(row)} for row in group_rows],
            "budget": asdict(
                self.budget_status(
                    workspace_id,
                    currency=normalized_currency,
                    now=end_at,
                )
            ),
        }

    def budget_status(
        self,
        workspace_id: UUID,
        *,
        currency: str = "USD",
        now: datetime | None = None,
    ) -> CostBudgetStatus:
        scope = resource_query_scope(self._session)
        if scope is not None and scope.workspace_id != workspace_id:
            raise ResourceAccessDenied()
        now = now or datetime.now(UTC)
        period_start, period_end = _month_window(now)
        normalized_currency = normalize_currency(currency)
        # Enforcement consumes the whole tenant ledger, including private/deleted tasks.
        # User-visible usage queries above retain resource filtering; budget totals cannot,
        # otherwise a restricted initiating user could bypass an exhausted workspace budget.
        budgets = WorkspaceCostBudget.__table__.c
        usage = ModelUsageRecord.__table__.c
        budget = self._session.execute(
            select(*budgets).where(
                budgets.workspace_id == workspace_id,
                budgets.currency == normalized_currency,
            )
        ).one_or_none()
        spent = self._session.scalar(
            select(func.coalesce(func.sum(usage.total_cost), 0)).where(
                usage.workspace_id == workspace_id,
                usage.currency == normalized_currency,
                usage.occurred_at >= period_start,
                usage.occurred_at < period_end,
            )
        )
        spent = Decimal(str(spent or 0))
        unpriced = int(
            self._session.scalar(
                select(func.count())
                .select_from(ModelUsageRecord.__table__)
                .where(
                    usage.workspace_id == workspace_id,
                    usage.metering_status != "priced",
                    or_(
                        usage.currency == normalized_currency,
                        usage.currency.is_(None),
                    ),
                    usage.occurred_at >= period_start,
                    usage.occurred_at < period_end,
                )
            )
            or 0
        )
        if budget is None or not budget.enabled:
            return CostBudgetStatus(
                state="unconfigured" if budget is None else "disabled",
                currency=normalized_currency,
                spent=spent,
                monthly_limit=None if budget is None else budget.monthly_limit,
                warning_ratio=None if budget is None else budget.warning_ratio,
                utilization_ratio=None,
                enforcement=None if budget is None else budget.enforcement,
                unpriced_records=unpriced,
                period_start=period_start,
                period_end=period_end,
            )
        ratio = spent / budget.monthly_limit
        state = "exhausted" if ratio >= 1 else "warning" if ratio >= budget.warning_ratio else "ok"
        return CostBudgetStatus(
            state=state,
            currency=normalized_currency,
            spent=spent,
            monthly_limit=budget.monthly_limit,
            warning_ratio=budget.warning_ratio,
            utilization_ratio=ratio,
            enforcement=budget.enforcement,
            unpriced_records=unpriced,
            period_start=period_start,
            period_end=period_end,
        )


def _month_window(value: datetime) -> tuple[datetime, datetime]:
    current = ensure_aware_utc(value)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _aggregate_columns(currency: str) -> tuple[Any, ...]:
    priced = and_(
        ModelUsageRecord.metering_status == "priced",
        ModelUsageRecord.currency == currency,
    )
    return (
        func.count().label("records"),
        func.coalesce(func.sum(case((priced, 1), else_=0)), 0).label("priced_records"),
        func.coalesce(func.sum(case((priced, 0), else_=1)), 0).label("unpriced_records"),
        func.coalesce(func.sum(ModelUsageRecord.request_count), 0).label("request_count"),
        func.coalesce(func.sum(ModelUsageRecord.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(ModelUsageRecord.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(ModelUsageRecord.cached_input_tokens), 0).label(
            "cached_input_tokens"
        ),
        func.coalesce(func.sum(ModelUsageRecord.reasoning_tokens), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(ModelUsageRecord.total_tokens), 0).label("total_tokens"),
        func.coalesce(
            func.sum(case((priced, ModelUsageRecord.total_cost), else_=Decimal("0"))),
            Decimal("0"),
        ).label("total_cost"),
    )


def _totals_from_row(row: Any) -> dict[str, int | Decimal]:
    return {
        "records": int(row["records"] or 0),
        "priced_records": int(row["priced_records"] or 0),
        "unpriced_records": int(row["unpriced_records"] or 0),
        "request_count": int(row["request_count"] or 0),
        "input_tokens": int(row["input_tokens"] or 0),
        "output_tokens": int(row["output_tokens"] or 0),
        "cached_input_tokens": int(row["cached_input_tokens"] or 0),
        "reasoning_tokens": int(row["reasoning_tokens"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
        "total_cost": Decimal(str(row["total_cost"] or 0)),
    }


def _group_expression(group_by: str) -> Any:
    if group_by == "provider":
        return ModelUsageRecord.provider
    if group_by == "model":
        return ModelUsageRecord.provider + "/" + ModelUsageRecord.model
    if group_by == "agent":
        return func.coalesce(cast(ModelUsageRecord.agent_profile_id, String), "unassigned")
    if group_by == "run":
        return cast(ModelUsageRecord.agent_run_id, String)
    if group_by == "day":
        return cast(func.date(ModelUsageRecord.occurred_at), String)
    raise ValueError("group_by must be provider, model, agent, run, or day")
