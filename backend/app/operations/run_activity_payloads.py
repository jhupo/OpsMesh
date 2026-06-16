from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_control_plane import OperationsRunActivityResponse
from backend.app.operations.run_activity_buckets import RunActivityBucketAccumulator
from backend.app.operations.run_activity_queries import RunActivityQueryService


class RunActivityPayloadService:
    def __init__(self, session: Session) -> None:
        self._queries = RunActivityQueryService(session)

    def run_activity_payload(
        self,
        workspace_id: UUID,
        *,
        team_id: UUID | None = None,
        scan_limit: int = 500,
    ) -> OperationsRunActivityResponse:
        now = datetime.now(UTC)
        page = self._queries.active_run_page(
            workspace_id,
            team_id=team_id,
            scan_limit=scan_limit,
        )
        latest_events = self._queries.latest_run_events_by_run_id(
            workspace_id,
            [run.id for run in page.runs],
        )
        buckets = RunActivityBucketAccumulator(now)
        for run in page.runs:
            buckets.add_run(run, latest_events.get(run.id))
        return OperationsRunActivityResponse(
            generated_at=now,
            team_id=team_id,
            total_active_runs=page.total_active_runs,
            scanned_active_runs=len(page.runs),
            truncated=page.total_active_runs > len(page.runs),
            status_counts=page.status_counts,
            phases=buckets.phase_responses(),
            oldest_active_run=buckets.oldest_active_run,
        )
