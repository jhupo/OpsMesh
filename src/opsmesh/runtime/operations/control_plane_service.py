from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from opsmesh.runtime.operations.contracts.control_plane import OperationsControlPlaneResponse
from opsmesh.runtime.operations.control_plane import (
    control_plane_health,
    control_plane_issues,
)
from opsmesh.runtime.operations.evidence import OperationsEvidenceService
from opsmesh.runtime.operations.operation_capacity_payloads import (
    OperationsCapacityPayloadService,
)
from opsmesh.runtime.operations.outcomes import OperationsOutcomeService
from opsmesh.runtime.operations.queues.latency import OperationsQueueLatencyService
from opsmesh.runtime.operations.scheduler import SchedulerBacklogService
from opsmesh.runtime.operations.workers.capacity import OperationsWorkerCapacityService
from opsmesh.runtime.operations.workers.self_hosted_machines import (
    OperationsSelfHostedMachineService,
)
from opsmesh.shared.redis.keys import RedisKeyBuilder


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
        audit_stale_after_seconds: int,
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
        evidence_service = OperationsEvidenceService(self._session)
        evidence = evidence_service.payload(
            workspace_id,
            audit_stale_after_seconds=audit_stale_after_seconds,
            now=now,
        )
        issues = control_plane_issues(
            queue=queue,
            worker_capacity=worker_capacity,
            runtime_capacity=runtime_capacity,
            scheduler=scheduler,
            outcomes=outcomes,
            mcp_jobs=mcp_jobs,
            self_hosted_machines=self_hosted_machines,
            evidence=evidence,
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
            evidence=evidence,
            drilldowns=evidence_service.drilldowns(workspace_id),
            issues=issues,
        )
