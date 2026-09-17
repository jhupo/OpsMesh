from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.utils import ensure_aware_utc
from backend.app.observability.audit.models import AuditIntegrityCheck
from backend.app.observability.costs.models import ModelUsageRecord, WorkspaceCostBudget
from backend.app.observability.costs.queries import CostQueryService
from backend.app.observability.notifications.models import WorkspaceNotification
from backend.app.runtime.operations.contracts.control_plane import (
    OperationsDrilldownsResponse,
    OperationsEvidencePlaneResponse,
    OperationsEvidenceSignalResponse,
)
from backend.app.runtime.operations.data_lifecycle_rollup import (
    WorkspaceDataLifecycleRollupService,
)


class OperationsEvidenceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def payload(
        self,
        workspace_id: UUID,
        *,
        audit_stale_after_seconds: int,
        now: datetime,
    ) -> OperationsEvidencePlaneResponse:
        return OperationsEvidencePlaneResponse(
            audit_integrity=self._audit_integrity(
                workspace_id,
                stale_after_seconds=audit_stale_after_seconds,
                now=now,
            ),
            cost_accounting=self._cost_accounting(workspace_id, now=now),
            notifications=self._notifications(workspace_id),
            data_lifecycle=WorkspaceDataLifecycleRollupService(
                self._session
            ).data_lifecycle_rollup(workspace_id),
        )

    def drilldowns(self, workspace_id: UUID) -> OperationsDrilldownsResponse:
        base = f"/api/v1/workspaces/{workspace_id}/operations"
        return OperationsDrilldownsResponse(
            api_metrics="/api/v1/metrics",
            queue=f"{base}/queue-insights",
            workers=f"{base}/worker-lifecycle",
            runtimes=f"{base}/runtime-capacity",
            mcp=f"/api/v1/workspaces/{workspace_id}/capabilities/mcp-tool-call-logs",
            approvals=f"/api/v1/workspaces/{workspace_id}/approvals",
            audit=f"{base}/audit-integrity",
            costs=f"/api/v1/workspaces/{workspace_id}/costs/summary",
            data_lifecycle=(
                f"/api/v1/workspaces/{workspace_id}/exports/recovery-readiness"
            ),
            notifications=f"/api/v1/workspaces/{workspace_id}/notifications",
        )

    def _audit_integrity(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int,
        now: datetime,
    ) -> OperationsEvidenceSignalResponse:
        latest = self._session.scalar(
            select(AuditIntegrityCheck)
            .where(AuditIntegrityCheck.workspace_id == workspace_id)
            .order_by(AuditIntegrityCheck.created_at.desc(), AuditIntegrityCheck.id.desc())
            .limit(1)
        )
        if latest is None:
            status = "missing"
        elif not latest.valid:
            status = "invalid"
        elif ensure_aware_utc(latest.created_at) < now - timedelta(
            seconds=stale_after_seconds
        ):
            status = "stale"
        else:
            status = "valid"
        return OperationsEvidenceSignalResponse(
            status=status,
            count=0 if status == "valid" else 1,
            last_observed_at=latest.created_at if latest is not None else None,
            metadata={
                "checked_events": latest.checked_events if latest is not None else 0,
                "broken_event_id": str(latest.broken_event_id)
                if latest is not None and latest.broken_event_id is not None
                else None,
                "reason": latest.reason if latest is not None else None,
            },
            recommended_actions=(
                []
                if status == "valid"
                else [
                    "Run an audit integrity verification."
                    if status in {"missing", "stale"}
                    else "Stop retention cleanup and investigate the broken audit range."
                ]
            ),
            drilldown=f"/api/v1/workspaces/{workspace_id}/operations/audit-integrity",
        )

    def _cost_accounting(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
    ) -> OperationsEvidenceSignalResponse:
        cutoff = now - timedelta(hours=24)
        rows = self._session.execute(
            select(ModelUsageRecord.metering_status, func.count())
            .where(
                ModelUsageRecord.workspace_id == workspace_id,
                ModelUsageRecord.occurred_at >= cutoff,
            )
            .group_by(ModelUsageRecord.metering_status)
        ).all()
        usage_counts = {str(state): int(count) for state, count in rows}
        latest_attempt = self._session.scalar(
            select(func.max(ModelUsageRecord.occurred_at)).where(
                ModelUsageRecord.workspace_id == workspace_id
            )
        )
        budget_states: dict[str, int] = {}
        blocking_exhausted = 0
        budgets = self._session.scalars(
            select(WorkspaceCostBudget).where(
                WorkspaceCostBudget.workspace_id == workspace_id,
                WorkspaceCostBudget.enabled.is_(True),
            )
        ).all()
        costs = CostQueryService(self._session)
        for budget in budgets:
            state = costs.budget_status(
                workspace_id,
                currency=budget.currency,
                now=now,
            ).state
            budget_states[state] = budget_states.get(state, 0) + 1
            if state == "exhausted" and budget.enforcement == "block":
                blocking_exhausted += 1
        incomplete = usage_counts.get("unpriced", 0) + usage_counts.get("missing_usage", 0)
        if blocking_exhausted:
            status = "critical"
        elif incomplete or budget_states.get("warning", 0) or budget_states.get("exhausted", 0):
            status = "warning"
        else:
            status = "healthy"
        actions: list[str] = []
        if usage_counts.get("unpriced", 0):
            actions.append("Configure pricing rules for unpriced model attempts.")
        if usage_counts.get("missing_usage", 0):
            actions.append("Verify provider SDK usage extraction.")
        if budget_states.get("warning", 0) or budget_states.get("exhausted", 0):
            actions.append("Review workspace budgets and high-cost runs.")
        return OperationsEvidenceSignalResponse(
            status=status,
            count=incomplete + budget_states.get("warning", 0) + budget_states.get(
                "exhausted", 0
            ),
            last_observed_at=latest_attempt,
            metadata={
                "usage_24h": usage_counts,
                "budget_states": budget_states,
                "blocking_exhausted": blocking_exhausted,
            },
            recommended_actions=actions,
            drilldown=f"/api/v1/workspaces/{workspace_id}/costs/summary",
        )

    def _notifications(self, workspace_id: UUID) -> OperationsEvidenceSignalResponse:
        rows = self._session.execute(
            select(WorkspaceNotification.severity, func.count())
            .where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.read_at.is_(None),
                WorkspaceNotification.archived_at.is_(None),
            )
            .group_by(WorkspaceNotification.severity)
        ).all()
        counts = {str(severity): int(count) for severity, count in rows}
        unread = sum(counts.values())
        latest = self._session.scalar(
            select(func.max(WorkspaceNotification.created_at)).where(
                WorkspaceNotification.workspace_id == workspace_id,
                WorkspaceNotification.read_at.is_(None),
                WorkspaceNotification.archived_at.is_(None),
            )
        )
        status = "critical" if counts.get("critical", 0) else "warning" if unread else "clear"
        return OperationsEvidenceSignalResponse(
            status=status,
            count=unread,
            last_observed_at=latest,
            metadata={"severity_counts": counts},
            recommended_actions=["Review unread governance notifications."] if unread else [],
            drilldown=f"/api/v1/workspaces/{workspace_id}/notifications?read=false",
        )
