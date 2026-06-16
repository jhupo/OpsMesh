from __future__ import annotations

from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_control_plane import OperationsOverviewResponse
from backend.app.operations.data_lifecycle_rollup import WorkspaceDataLifecycleRollupService
from backend.app.operations.overview_queries import OperationsOverviewQueryService
from backend.app.operations.queue_metrics import QueueMetricsService
from backend.app.redis.keys import RedisKeyBuilder


class OperationsOverviewPayloadService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str],
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._queries = OperationsOverviewQueryService(session)
        self._queue_metrics = QueueMetricsService(redis, key_builder)
        self._data_lifecycle = WorkspaceDataLifecycleRollupService(session)

    def overview_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
    ) -> OperationsOverviewResponse:
        return OperationsOverviewResponse(
            queue=self._queue_metrics.queue_metrics(queue_name, workspace_id),
            failed_runs=self._queries.failed_runs_count(workspace_id),
            offline_runtimes=self._queries.offline_runtimes_count(workspace_id),
            workers_online=self._queries.workers_online_count(workspace_id),
            security_warnings=self._queries.security_warnings_count(workspace_id),
            data_lifecycle=self._data_lifecycle.data_lifecycle_rollup(workspace_id),
        )
