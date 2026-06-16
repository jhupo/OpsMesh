from __future__ import annotations

from backend.app.api.schemas.operation_queue import QueueGovernanceDiagnosticsResponse
from backend.app.operations.queue_governance_policy import (
    queue_governance_issues,
    recommended_actions,
)
from backend.app.operations.queue_governance_snapshot_models import QueueGovernanceSnapshot


def queue_governance_response(
    snapshot: QueueGovernanceSnapshot,
) -> QueueGovernanceDiagnosticsResponse:
    return QueueGovernanceDiagnosticsResponse(
        generated_at=snapshot.generated_at,
        queue_name=snapshot.queue_name,
        scan_limit=snapshot.scan_limit,
        stale_after_seconds=snapshot.stale_after_seconds,
        queued_total=snapshot.queued_total,
        queued_scanned=snapshot.queued_scanned,
        agent_run_jobs_scanned=len(snapshot.agent_run_jobs),
        dead_letter_total=snapshot.dead_letter_total,
        orphaned_queue_jobs=len(snapshot.orphaned_jobs),
        non_runnable_queue_jobs=len(snapshot.non_runnable_jobs),
        duplicate_queue_jobs=len(snapshot.duplicate_jobs),
        queued_runs_missing_queue_job=len(snapshot.missing_runs),
        old_queued_jobs=len(snapshot.old_queued_jobs),
        truncated=snapshot.truncated,
        issues=queue_governance_issues(snapshot),
        recommended_actions=recommended_actions(snapshot),
    )
