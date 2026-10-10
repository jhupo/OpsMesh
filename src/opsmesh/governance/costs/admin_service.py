"""Cross-workspace ledger aggregation, available only through administrator routes."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from opsmesh.governance.costs.models import ModelUsageRecord
from opsmesh.governance.costs.pricing import normalize_currency
from opsmesh.governance.costs.queries import (
    _aggregate_columns,
    _group_expression,
    _totals_from_row,
)


class AdminCostService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def summary(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        currency: str,
        group_by: str,
        workspace_id: UUID | None,
        limit: int,
        offset: int,
    ) -> dict[str, object]:
        currency = normalize_currency(currency)
        filters = [
            ModelUsageRecord.occurred_at >= start_at,
            ModelUsageRecord.occurred_at < end_at,
            or_(ModelUsageRecord.currency == currency, ModelUsageRecord.currency.is_(None)),
        ]
        if workspace_id is not None:
            filters.append(ModelUsageRecord.workspace_id == workspace_id)
        columns = _aggregate_columns(currency)
        expression = (
            cast(ModelUsageRecord.workspace_id, String)
            if group_by == "workspace"
            else _group_expression(group_by)
        )
        totals = self._session.execute(select(*columns).where(*filters)).mappings().one()
        groups = (
            self._session.execute(
                select(expression.label("key"), *columns)
                .where(*filters)
                .group_by(expression)
                .order_by(expression)
                .limit(limit + 1)
                .offset(offset)
            )
            .mappings()
            .all()
        )
        return {
            "start_at": start_at,
            "end_at": end_at,
            "currency": currency,
            "group_by": group_by,
            "workspace_id": workspace_id,
            "totals": _totals_from_row(totals),
            "groups": [{"key": str(row["key"]), **_totals_from_row(row)} for row in groups[:limit]],
            "limit": limit,
            "offset": offset,
            "has_more": len(groups) > limit,
        }
