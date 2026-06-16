from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.operations.queue_governance_queue_scan import QueueGovernanceQueueScanner
from backend.app.operations.queue_governance_run_drift import (
    duplicate_jobs,
    non_runnable_jobs,
    orphaned_jobs,
    queued_run_ids,
)
from backend.app.operations.queue_governance_run_repository import QueueGovernanceRunRepository
from backend.app.operations.queue_governance_snapshot_models import QueueGovernanceSnapshot
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.queue import RedisQueue


class QueueGovernanceSnapshotBuilder:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._runs = QueueGovernanceRunRepository(session)
        self._redis = redis
        self._keys = key_builder

    def build(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int,
        stale_after_seconds: int,
    ) -> QueueGovernanceSnapshot:
        now = datetime.now(UTC)
        queue = RedisQueue(self._redis, self._keys, queue_name)
        scan = QueueGovernanceQueueScanner(queue).scan(
            workspace_id=workspace_id,
            scan_limit=scan_limit,
            generated_at=now,
            stale_after_seconds=stale_after_seconds,
        )
        runs_by_id = self._runs.runs_by_id(
            workspace_id,
            {job.resource_id for job in scan.agent_run_jobs},
        )
        queued_ids = queued_run_ids(scan.agent_run_jobs, runs_by_id)
        return QueueGovernanceSnapshot(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            stale_after_seconds=stale_after_seconds,
            queued_total=scan.queued_total,
            queued_scanned=len(scan.workspace_jobs),
            agent_run_jobs=scan.agent_run_jobs,
            dead_letter_total=scan.dead_letter_total,
            orphaned_jobs=orphaned_jobs(scan.agent_run_jobs, runs_by_id),
            non_runnable_jobs=non_runnable_jobs(scan.agent_run_jobs, runs_by_id),
            duplicate_jobs=duplicate_jobs(scan.agent_run_jobs),
            missing_runs=self._runs.queued_runs_missing_jobs(
                workspace_id=workspace_id,
                queued_run_ids=queued_ids,
                truncated=scan.truncated,
            ),
            old_queued_jobs=scan.old_queued_jobs,
            truncated=scan.truncated,
        )
