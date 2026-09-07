from __future__ import annotations

from datetime import UTC, datetime

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.metrics import GaugeMetric
from backend.app.operations.observability_constants import (
    PROMETHEUS_QUEUE_SCAN_LIMIT,
    PROMETHEUS_WORKER_STALE_AFTER_SECONDS,
)
from backend.app.operations.prometheus_governance_metrics import GovernancePrometheusMetrics
from backend.app.operations.prometheus_runtime_metrics import RuntimePrometheusMetrics
from backend.app.operations.prometheus_team_runtime_metrics import TeamRuntimePrometheusMetrics
from backend.app.operations.prometheus_worker_metrics import WorkerPrometheusMetrics
from backend.app.operations.queue_metrics import QueueMetricsService
from backend.app.redis.keys import RedisKeyBuilder


class OperationsPrometheusMetricsService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._queue = QueueMetricsService(redis, key_builder)

    def prometheus_gauges(
        self,
        queue_name: str,
        *,
        worker_stale_after_seconds: int = PROMETHEUS_WORKER_STALE_AFTER_SECONDS,
        queue_scan_limit: int = PROMETHEUS_QUEUE_SCAN_LIMIT,
        audit_integrity_stale_after_seconds: int = 7_200,
    ) -> list[GaugeMetric]:
        now = datetime.now(UTC)
        gauges: list[GaugeMetric] = []
        try:
            queue = self._queue.queue_metrics(queue_name)
            gauges.extend(
                [
                    GaugeMetric(
                        "opsmesh_queue_jobs",
                        queue.queued,
                        labels={"queue_name": queue_name, "state": "queued"},
                        help_text="Jobs currently waiting in Redis queues.",
                    ),
                    GaugeMetric(
                        "opsmesh_queue_jobs",
                        queue.dead_letter,
                        labels={"queue_name": queue_name, "state": "dead_letter"},
                        help_text="Jobs currently waiting in Redis queues.",
                    ),
                    GaugeMetric(
                        "opsmesh_queue_idempotency_keys",
                        queue.idempotency_keys,
                        labels={"queue_name": queue_name},
                        help_text="Active Redis idempotency keys for queued work.",
                    ),
                    GaugeMetric(
                        "opsmesh_queue_oldest_queued_age_seconds",
                        self._queue.oldest_queued_age_seconds(
                            queue_name,
                            now,
                            queue_scan_limit,
                        )
                        or 0,
                        labels={"queue_name": queue_name},
                        help_text="Age of the oldest queued job seen in the queue scan.",
                    ),
                ]
            )
        except (OSError, RedisError, TimeoutError):
            gauges.append(_collection_status("redis", success=False))
        else:
            gauges.append(_collection_status("redis", success=True))
        try:
            gauges.extend(
                WorkerPrometheusMetrics(self._session).gauges(now, worker_stale_after_seconds)
            )
            gauges.extend(RuntimePrometheusMetrics(self._session).gauges())
            gauges.extend(TeamRuntimePrometheusMetrics(self._session).gauges(now))
            gauges.extend(
                GovernancePrometheusMetrics(self._session).gauges(
                    now,
                    audit_stale_after_seconds=audit_integrity_stale_after_seconds,
                )
            )
        except SQLAlchemyError:
            gauges.append(_collection_status("postgres", success=False))
        else:
            gauges.append(_collection_status("postgres", success=True))
        return gauges


def _collection_status(source: str, *, success: bool) -> GaugeMetric:
    return GaugeMetric(
        "opsmesh_metrics_collection_success",
        int(success),
        labels={"source": source},
        help_text="Whether the latest domain metrics collection succeeded.",
    )
