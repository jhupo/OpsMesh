from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from redis import Redis

from backend.app.api.schemas.operation_queue import OperationsQueueInsightsResponse
from backend.app.execution.operations.queue_insight_buckets import QueueInsightBucketBuilder
from backend.app.execution.operations.utils import age_seconds
from backend.app.execution.workers.jobs import JobPayload
from backend.app.execution.workers.redis_queue import RedisQueue
from backend.app.platform.redis.keys import RedisKeyBuilder


@dataclass(frozen=True, slots=True)
class QueueInsightScan:
    queued_total: int
    dead_letter_total: int
    queued_jobs: list[JobPayload]
    dead_letter_jobs: list[JobPayload]

    @property
    def truncated(self) -> bool:
        return self.queued_total > len(self.queued_jobs) or self.dead_letter_total > len(
            self.dead_letter_jobs
        )







class QueueInsightScanner:
    def __init__(
        self,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._redis = redis
        self._keys = key_builder

    def scan(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int,
    ) -> QueueInsightScan:
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return QueueInsightScan(
            queued_total=queue.count_queued(workspace_id=workspace_id),
            dead_letter_total=queue.count_dead_letters(workspace_id=workspace_id),
            queued_jobs=[
                job for job in queue.peek(limit=scan_limit) if job.workspace_id == workspace_id
            ],
            dead_letter_jobs=queue.list_dead_letters(scan_limit, workspace_id=workspace_id),
        )







class QueueInsightsService:
    def __init__(
        self,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._scanner = QueueInsightScanner(redis, key_builder)

    def queue_insights(
        self,
        *,
        workspace_id: UUID,
        queue_name: str,
        scan_limit: int = 500,
    ) -> OperationsQueueInsightsResponse:
        now = datetime.now(UTC)
        scan = self._scanner.scan(
            workspace_id=workspace_id,
            queue_name=queue_name,
            scan_limit=scan_limit,
        )
        buckets = QueueInsightBucketBuilder(now)
        for job in scan.queued_jobs:
            buckets.add_queued_job(job)
        for job in scan.dead_letter_jobs:
            buckets.add_dead_letter_job(job)

        return OperationsQueueInsightsResponse(
            generated_at=now,
            queue_name=queue_name,
            scan_limit=scan_limit,
            queued_total=scan.queued_total,
            dead_letter_total=scan.dead_letter_total,
            queued_scanned=len(scan.queued_jobs),
            dead_letter_scanned=len(scan.dead_letter_jobs),
            truncated=scan.truncated,
            oldest_queued_age_seconds=max(
                [age_seconds(now, job.created_at) or 0 for job in scan.queued_jobs],
                default=None,
            ),
            highest_priority=max([job.priority for job in scan.queued_jobs], default=None),
            priority_buckets=buckets.priority_responses(),
            job_type_buckets=buckets.job_type_responses(),
        )
