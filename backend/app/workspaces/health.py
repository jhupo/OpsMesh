from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.typing import dict_list, dict_or_empty, string_list
from backend.app.workspaces.health_collector import WorkspaceHealthCollector
from backend.app.workspaces.health_metrics import (
    delivery_summary,
    execution_summary,
    health_score,
    health_status,
    recommended_actions,
    risk_items,
    task_summary,
)
from backend.app.workspaces.health_policy import (
    aware_datetime,
    health_snapshot_policy,
    snapshot_due,
)
from backend.app.workspaces.health_trends import (
    recommendation_changes,
    risk_changes,
    score_delta,
    snapshot_payload,
    status_change,
)
from backend.app.workspaces.models import Workspace, WorkspaceHealthSnapshot


class WorkspaceHealthResult(TypedDict):
    workspace_id: UUID
    generated_at: datetime
    status: str
    score: int
    summary: dict[str, object]
    risk_items: list[dict[str, object]]
    recommended_actions: list[str]
    trend_basis: dict[str, object]


@dataclass(frozen=True)
class WorkspaceHealthSnapshotMaintenanceSummary:
    workspaces_scanned: int = 0
    snapshots_created: int = 0
    snapshots_skipped: int = 0
    snapshots_disabled: int = 0


class WorkspaceHealthService:
    """Compute workspace-level product operations health from current state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_health(self, workspace_id: UUID) -> WorkspaceHealthResult:
        collected = WorkspaceHealthCollector(self._session).collect(workspace_id)
        delivery = delivery_summary(collected.tasks, collected.steps, collected.artifacts)
        execution = execution_summary(collected.runs)
        tasks = task_summary(collected.tasks)
        control = {
            "control_message_count": len(collected.control_messages),
            "latest_control_sequence": max(
                (message.sequence for message in collected.control_messages),
                default=0,
            ),
        }
        risks = risk_items(tasks, execution, delivery, control)
        score = health_score(risks)
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "status": health_status(score, risks),
            "score": score,
            "summary": {
                "team_count": collected.team_count,
                **tasks,
                **execution,
                **delivery,
                **control,
            },
            "risk_items": risks,
            "recommended_actions": recommended_actions(risks),
            "trend_basis": {
                "mode": "snapshot",
                "message": "Use health snapshots to persist and compare this score over time.",
            },
        }

    def record_snapshot(self, workspace_id: UUID) -> WorkspaceHealthSnapshot:
        health = self.get_health(workspace_id)
        snapshot = WorkspaceHealthSnapshot(
            workspace_id=workspace_id,
            status=health["status"],
            score=health["score"],
            summary=dict_or_empty(health.get("summary")),
            risk_items=dict_list(health.get("risk_items")),
            recommended_actions=string_list(health.get("recommended_actions")),
            trend_basis={
                **dict_or_empty(health.get("trend_basis")),
                "mode": "persisted_snapshot",
            },
        )
        self._session.add(snapshot)
        self._session.commit()
        self._session.refresh(snapshot)
        return snapshot

    def list_snapshots(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[WorkspaceHealthSnapshot], int]:
        statement = (
            select(WorkspaceHealthSnapshot)
            .where(WorkspaceHealthSnapshot.workspace_id == workspace_id)
            .order_by(WorkspaceHealthSnapshot.created_at.desc(), WorkspaceHealthSnapshot.id.desc())
        )
        total = len(
            self._session.scalars(
                select(WorkspaceHealthSnapshot.id).where(
                    WorkspaceHealthSnapshot.workspace_id == workspace_id
                )
            ).all()
        )
        items = list(self._session.scalars(statement.limit(limit).offset(offset)).all())
        return items, total

    def get_trends(self, workspace_id: UUID, *, limit: int = 20) -> dict[str, object]:
        snapshots, total = self.list_snapshots(workspace_id, limit=limit, offset=0)
        latest = snapshots[0] if snapshots else None
        previous = snapshots[1] if len(snapshots) > 1 else None
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "snapshot_count": total,
            "compared_snapshot_count": len(snapshots),
            "latest": snapshot_payload(latest),
            "previous": snapshot_payload(previous),
            "score_delta": score_delta(latest, previous),
            "status_change": status_change(latest, previous),
            "risk_changes": risk_changes(latest, previous),
            "recommendation_changes": recommendation_changes(latest, previous),
        }

    def run_scheduled_snapshots(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> WorkspaceHealthSnapshotMaintenanceSummary:
        current_time = aware_datetime(now or datetime.now(UTC))
        workspaces = list(
            self._session.scalars(
                select(Workspace)
                .where(Workspace.status == "active")
                .order_by(Workspace.created_at.asc(), Workspace.id.asc())
                .limit(max(1, limit))
            ).all()
        )
        created = 0
        skipped = 0
        disabled = 0
        for workspace in workspaces:
            policy = health_snapshot_policy(workspace.settings)
            if policy is None:
                disabled += 1
                continue
            latest = self._latest_snapshot(workspace.id)
            if latest is not None and not snapshot_due(
                latest.created_at,
                current_time,
                policy.interval,
            ):
                skipped += 1
                continue
            self.record_snapshot(workspace.id)
            created += 1
        return WorkspaceHealthSnapshotMaintenanceSummary(
            workspaces_scanned=len(workspaces),
            snapshots_created=created,
            snapshots_skipped=skipped,
            snapshots_disabled=disabled,
        )

    def _latest_snapshot(self, workspace_id: UUID) -> WorkspaceHealthSnapshot | None:
        return self._session.scalar(
            select(WorkspaceHealthSnapshot)
            .where(WorkspaceHealthSnapshot.workspace_id == workspace_id)
            .order_by(
                WorkspaceHealthSnapshot.created_at.desc(),
                WorkspaceHealthSnapshot.id.desc(),
            )
            .limit(1)
        )
