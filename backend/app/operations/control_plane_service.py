from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_control_plane import OperationsControlPlaneResponse
from backend.app.operations.control_plane_health import control_plane_health
from backend.app.operations.control_plane_issues import control_plane_issues
from backend.app.operations.operation_capacity_payloads import OperationsCapacityPayloadService
from backend.app.operations.outcomes import OperationsOutcomeService
from backend.app.operations.queue_latency import OperationsQueueLatencyService
from backend.app.operations.scheduler_backlog import SchedulerBacklogService
from backend.app.operations.self_hosted_machine_payloads import (
    OperationsSelfHostedMachineService,
)
from backend.app.operations.worker_capacity_summary import OperationsWorkerCapacityService
from backend.app.redis.keys import RedisKeyBuilder


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
