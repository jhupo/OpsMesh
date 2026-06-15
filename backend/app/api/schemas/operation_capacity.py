from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from backend.app.api.schemas.operation_queue import QueueLatencyResponse


class WorkerCapacityAggregateResponse(BaseModel):
    workers_total: int
    workers_online: int
    workers_draining: int
    workers_offline: int
    max_jobs: int
    running_jobs: int
    available_slots: int
    utilization: float


class RuntimeSpaceQuotaUsageResponse(BaseModel):
    quota_key: str
    limit_value: int
    reserved_value: int
    unit: str
    utilization: float
    saturated: bool


class RuntimeSpaceSaturationResponse(BaseModel):
    runtime_space_id: UUID
    name: str
    status: str
    active_runtimes: int
    quotas: list[RuntimeSpaceQuotaUsageResponse]
    saturated: bool


class OperationsCapacityResponse(BaseModel):
    generated_at: datetime
    queue: QueueLatencyResponse
    worker_capacity: WorkerCapacityAggregateResponse
    runtime_spaces: list[RuntimeSpaceSaturationResponse]


class RuntimeProviderCapacityResponse(BaseModel):
    provider: str
    runtime_type: str
    total: int
    online: int
    offline: int
    degraded: int
    running: int
    capacity_slots: int
    active_runs: int
    utilization: float


class WorkerTypeCapacityResponse(BaseModel):
    worker_type: str
    workers_total: int
    workers_online: int
    workers_draining: int
    max_jobs: int
    running_jobs: int
    available_slots: int
    utilization: float


class WorkerLifecycleBucketResponse(BaseModel):
    worker_type: str
    queued_jobs: int
    running_jobs: int
    completed_jobs: int
    failed_jobs: int
    retried_jobs: int
    expired_jobs: int
    failure_rate: float
    average_duration_seconds: int | None
    oldest_queued_age_seconds: int | None
    oldest_running_age_seconds: int | None


class OperationsWorkerLifecycleResponse(BaseModel):
    generated_at: datetime
    queue_name: str
    worker_types: list[WorkerLifecycleBucketResponse]


class OperationsRuntimeCapacityResponse(BaseModel):
    generated_at: datetime
    providers: list[RuntimeProviderCapacityResponse]
    worker_types: list[WorkerTypeCapacityResponse]
    runtime_spaces: list[RuntimeSpaceSaturationResponse]
