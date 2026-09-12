from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations.control_plane import OperationsControlPlaneResponse
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.runtime.operations.control_plane import (
    control_plane_health,
    control_plane_issues,
)
from backend.app.runtime.operations.operation_capacity_payloads import (
    OperationsCapacityPayloadService,
)
from backend.app.runtime.operations.outcomes import OperationsOutcomeService
from backend.app.runtime.operations.queues.latency import OperationsQueueLatencyService
from backend.app.runtime.operations.scheduler import SchedulerBacklogService
from backend.app.runtime.operations.workers.capacity import OperationsWorkerCapacityService
from backend.app.runtime.operations.workers.self_hosted_machines import (
    OperationsSelfHostedMachineService,
)


class OperationsControlPlaneService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

    def control_plane_payload(
        self,
        workspace_id: UUID,
        queue_name: str,
        *,
        window_seconds: int,
    ) -> OperationsControlPlaneResponse:
        now = datetime.now(UTC)
        queue = OperationsQueueLatencyService(self._redis, self._keys).queue_latency(
            queue_name,
            workspace_id,
        )
        worker_capacity = OperationsWorkerCapacityService(
            self._session
        ).worker_capacity_aggregate()
        runtime_capacity = OperationsCapacityPayloadService(
            self._session,
            self._redis,
            self._keys,
        ).runtime_capacity_payload(workspace_id)
        scheduler = SchedulerBacklogService(self._session).scheduler_payload(workspace_id)
        outcome_service = OperationsOutcomeService(self._session)
        outcomes = outcome_service.outcomes_payload(workspace_id, window_seconds=window_seconds)
        mcp_jobs = outcome_service.mcp_jobs_payload(workspace_id)
        self_hosted_machines = OperationsSelfHostedMachineService(
            self._session
        ).self_hosted_machines_payload(workspace_id)
        issues = control_plane_issues(
            queue=queue,
            worker_capacity=worker_capacity,
            runtime_capacity=runtime_capacity,
            scheduler=scheduler,
            outcomes=outcomes,
            mcp_jobs=mcp_jobs,
            self_hosted_machines=self_hosted_machines,
        )
        return OperationsControlPlaneResponse(
            generated_at=now,
            health=control_plane_health(issues),
            queue=queue,
            worker_capacity=worker_capacity,
            runtime_capacity=runtime_capacity,
            scheduler=scheduler,
            outcomes=outcomes,
            mcp_jobs=mcp_jobs,
            self_hosted_machines=self_hosted_machines,
            issues=issues,
        )
