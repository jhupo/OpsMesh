from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy.orm import Session

from opsmesh.runtime.operations.contracts.capacity import (
    OperationsCapacityResponse,
    OperationsRuntimeCapacityResponse,
)
from opsmesh.runtime.operations.queues.latency import OperationsQueueLatencyService
from opsmesh.runtime.operations.runtimes.provider_capacity import (
    RuntimeProviderCapacityService,
)
from opsmesh.runtime.operations.runtimes.space_saturation import RuntimeSpaceSaturationService
from opsmesh.runtime.operations.workers.capacity import OperationsWorkerCapacityService
from opsmesh.shared.redis.keys import RedisKeyBuilder


class OperationsCapacityPayloadService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._queue_latency = OperationsQueueLatencyService(redis, key_builder)
        self._worker_capacity = OperationsWorkerCapacityService(session)
        self._runtime_providers = RuntimeProviderCapacityService(session)
        self._runtime_spaces = RuntimeSpaceSaturationService(session)

    def capacity_payload(self, workspace_id: UUID, queue_name: str) -> OperationsCapacityResponse:
        return OperationsCapacityResponse(
            generated_at=datetime.now(UTC),
            queue=self._queue_latency.queue_latency(queue_name, workspace_id),
            worker_capacity=self._worker_capacity.worker_capacity_aggregate(),
            runtime_spaces=self._runtime_spaces.runtime_space_saturation(workspace_id),
        )

    def runtime_capacity_payload(self, workspace_id: UUID) -> OperationsRuntimeCapacityResponse:
        return OperationsRuntimeCapacityResponse(
            generated_at=datetime.now(UTC),
            providers=self._runtime_providers.runtime_provider_capacity(workspace_id),
            worker_types=self._worker_capacity.worker_type_capacity(),
            runtime_spaces=self._runtime_spaces.runtime_space_saturation(workspace_id),
        )
