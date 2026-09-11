from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.observability.audit_models import AuditIntegrityCheck
from backend.app.core.metrics import GaugeMetric
from backend.app.observability.cost_models import ModelUsageRecord, WorkspaceCostBudget
from backend.app.workspaces.models import Workspace


class GovernancePrometheusMetrics:
    def __init__(self, session: Session) -> None:
        self._session = session

    def gauges(
        self,
        now: datetime,
        *,
        audit_stale_after_seconds: int,
    ) -> list[GaugeMetric]:
        return [
            *self._audit_integrity_gauges(now, audit_stale_after_seconds),
            *self._cost_gauges(now),
        ]

    def _audit_integrity_gauges(
        self,
        now: datetime,
        stale_after_seconds: int,
    ) -> list[GaugeMetric]:
        workspace_ids = set(self._session.scalars(select(Workspace.id)).all())
        latest_by_workspace: dict[object, AuditIntegrityCheck] = {}
        ranked_checks = select(
            AuditIntegrityCheck.id.label("id"),
            func.row_number()
            .over(
                partition_by=AuditIntegrityCheck.workspace_id,
                order_by=(
                    AuditIntegrityCheck.created_at.desc(),
                    AuditIntegrityCheck.id.desc(),
                ),
            )
            .label("rank"),
        ).subquery()
        checks = self._session.scalars(
            select(AuditIntegrityCheck)
            .join(ranked_checks, ranked_checks.c.id == AuditIntegrityCheck.id)
            .where(ranked_checks.c.rank == 1)
        ).all()
        for check in checks:
            latest_by_workspace.setdefault(check.workspace_id, check)
        cutoff = now - timedelta(seconds=stale_after_seconds)
        states = {"valid": 0, "invalid": 0, "stale": 0, "missing": 0}
        for workspace_id in workspace_ids:
            latest = latest_by_workspace.get(workspace_id)
            if latest is None:
                states["missing"] += 1
            elif not latest.valid:
                states["invalid"] += 1
            elif _as_utc(latest.created_at) < cutoff:
                states["stale"] += 1
            else:
                states["valid"] += 1
        return [
            GaugeMetric(
                "opsmesh_audit_integrity_workspaces",
                count,
                labels={"state": state},
                help_text="Workspace audit hash-chain integrity state.",
            )
            for state, count in states.items()
        ]

    def _cost_gauges(self, now: datetime) -> list[GaugeMetric]:
        recent_cutoff = now - timedelta(hours=24)
        usage_states = {"priced": 0, "unpriced": 0, "missing_usage": 0}
        rows = self._session.execute(
            select(ModelUsageRecord.metering_status, func.count())
            .where(ModelUsageRecord.occurred_at >= recent_cutoff)
            .group_by(ModelUsageRecord.metering_status)
        ).all()
        for state, count in rows:
            usage_states[str(state)] = int(count)

        budget_states = {"ok": 0, "warning": 0, "exhausted": 0}
        budgets = self._session.scalars(
            select(WorkspaceCostBudget).where(WorkspaceCostBudget.enabled.is_(True))
        ).all()
        period_start = now.astimezone(UTC).replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        workspace_cost_rows = self._session.execute(
            select(
                ModelUsageRecord.workspace_id,
                ModelUsageRecord.currency,
                func.coalesce(func.sum(ModelUsageRecord.total_cost), 0),
            )
            .where(
                ModelUsageRecord.occurred_at >= period_start,
                ModelUsageRecord.currency.is_not(None),
            )
            .group_by(ModelUsageRecord.workspace_id, ModelUsageRecord.currency)
        ).all()
        spent_by_budget = {
            (workspace_id, str(currency)): Decimal(str(total))
            for workspace_id, currency, total in workspace_cost_rows
        }
        for budget in budgets:
            spent = spent_by_budget.get(
                (budget.workspace_id, budget.currency),
                Decimal("0"),
            )
            ratio = spent / budget.monthly_limit
            state = (
                "exhausted" if ratio >= 1 else "warning" if ratio >= budget.warning_ratio else "ok"
            )
            budget_states[state] += 1

        cost_rows = self._session.execute(
            select(
                ModelUsageRecord.currency, func.coalesce(func.sum(ModelUsageRecord.total_cost), 0)
            )
            .where(
                ModelUsageRecord.occurred_at >= period_start,
                ModelUsageRecord.currency.is_not(None),
            )
            .group_by(ModelUsageRecord.currency)
        ).all()
        gauges = [
            GaugeMetric(
                "opsmesh_model_usage_records_24h",
                count,
                labels={"state": state},
                help_text="Model usage records observed during the last 24 hours.",
            )
            for state, count in usage_states.items()
        ]
        gauges.extend(
            GaugeMetric(
                "opsmesh_cost_budget_workspaces",
                count,
                labels={"state": state},
                help_text="Enabled workspace model-cost budget states.",
            )
            for state, count in budget_states.items()
        )
        gauges.extend(
            GaugeMetric(
                "opsmesh_model_cost_current_month",
                float(Decimal(str(total))),
                labels={"currency": str(currency)},
                help_text="Priced model usage cost for the current UTC month.",
            )
            for currency, total in cost_rows
        )
        return gauges


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
