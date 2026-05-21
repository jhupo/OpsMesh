from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.audit import AuditEventResponse
from backend.app.api.schemas.common import ORMModel, TimestampedModel
from backend.app.api.schemas.runs import AgentRunResponse, RunEventResponse
from backend.app.workers.jobs import JobPayload


class RuntimeEventResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    workspace_runtime_id: UUID
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime


class SecurityEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID | None
    user_id: UUID | None
    action: str
    outcome: str
    severity: str
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    path: str
    method: str
    reason: str
    event_metadata: dict[str, object]
    created_at: datetime


class WorkerHeartbeatRequest(BaseModel):
    workspace_id: UUID | None = None
    worker_id: str = Field(min_length=1, max_length=160)
    worker_type: str = Field(default="cloud", max_length=80)
    status: str = Field(default="online", max_length=32)
    queue_name: str = Field(default="agent_runs", max_length=120)
    worker_version: str | None = Field(default=None, max_length=120)
    hostname: str | None = Field(default=None, max_length=255)
    capacity: dict[str, object] = Field(default_factory=dict)
    details: dict[str, object] = Field(default_factory=dict)


class WorkerHeartbeatResponse(TimestampedModel):
    workspace_id: UUID | None
    worker_id: str
    worker_type: str
    status: str
    queue_name: str
    details: dict[str, object]
    last_seen_at: datetime


class WorkerNodeResponse(TimestampedModel):
    worker_id: str
    worker_type: str
    status: str
    queue_name: str
    worker_version: str | None
    hostname: str | None
    capacity: dict[str, object]
    details: dict[str, object]
    drain_requested_at: datetime | None
    last_seen_at: datetime


class WorkerLeaseResponse(TimestampedModel):
    workspace_id: UUID
    worker_id: str
    queue_name: str
    job_id: UUID
    job_type: str
    resource_id: UUID
    status: str
    attempt: int
    lease_metadata: dict[str, object]
    started_at: datetime
    finished_at: datetime | None


class RuntimeLeaseResponse(TimestampedModel):
    workspace_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    docker_container_id: str | None
    status: str
    lease_metadata: dict[str, object]
    acquired_at: datetime
    released_at: datetime | None


class QueueMetricsResponse(BaseModel):
    queue_name: str
    queued: int
    dead_letter: int
    idempotency_keys: int


class DeadLetterJobsResponse(BaseModel):
    items: list[JobPayload]
    total: int


class RequeueDeadLetterResponse(BaseModel):
    requeued: bool
    job: JobPayload | None = None


class RuntimeCleanupResponse(BaseModel):
    stale_marked_offline: int
    deleted_records: int
    expired_worker_leases: int = 0


class FailedJobInspectionResponse(BaseModel):
    runs: list[AgentRunResponse]
    total: int


class OperationsOverviewResponse(BaseModel):
    queue: QueueMetricsResponse
    failed_runs: int
    offline_runtimes: int
    workers_online: int
    security_warnings: int


class QueueLatencyResponse(BaseModel):
    queue_name: str
    queued: int
    oldest_age_seconds: int | None
    newest_age_seconds: int | None
    average_age_seconds: int | None
    highest_priority: int | None


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


class OperationsRuntimeCapacityResponse(BaseModel):
    generated_at: datetime
    providers: list[RuntimeProviderCapacityResponse]
    worker_types: list[WorkerTypeCapacityResponse]
    runtime_spaces: list[RuntimeSpaceSaturationResponse]


class SchedulerBacklogResponse(BaseModel):
    queued_steps: int
    running_steps: int
    waiting_approval_tasks: int
    blocked_steps: int
    active_runs: int
    oldest_queued_age_seconds: int | None
    highest_priority: int | None


class SchedulerPriorityBucketResponse(BaseModel):
    priority: int
    queued_steps: int
    running_steps: int
    blocked_steps: int


class SchedulerBlockedReasonResponse(BaseModel):
    reason: str
    count: int


class SchedulerPolicyResponse(BaseModel):
    max_active_runs: int | None
    max_running_tasks: int | None
    max_runs_to_start_per_tick: int | None
    max_steps_per_task_per_tick: int | None
    starvation_boost_after_seconds: int | None
    resource_limits: dict[str, float]


class OperationsSchedulerResponse(BaseModel):
    generated_at: datetime
    backlog: SchedulerBacklogResponse
    priority_buckets: list[SchedulerPriorityBucketResponse]
    blocked_reasons: list[SchedulerBlockedReasonResponse]
    policy: SchedulerPolicyResponse


class RunFailureReasonResponse(BaseModel):
    code: str
    count: int


class RunOutcomeWindowResponse(BaseModel):
    window_seconds: int
    total_runs: int
    completed_runs: int
    failed_runs: int
    cancelled_runs: int
    failure_rate: float
    failure_reasons: list[RunFailureReasonResponse]


class ApprovalBacklogResponse(BaseModel):
    pending: int
    high_risk_pending: int
    oldest_pending_age_seconds: int | None
    pending_by_type: dict[str, int]


class OperationsOutcomesResponse(BaseModel):
    generated_at: datetime
    runs: RunOutcomeWindowResponse
    approvals: ApprovalBacklogResponse


class McpJobStatusBucketResponse(BaseModel):
    status: str
    count: int


class McpJobToolBucketResponse(BaseModel):
    tool_name: str
    queued: int
    claimed: int
    completed: int
    failed: int
    total: int


class OperationsMcpJobsResponse(BaseModel):
    generated_at: datetime
    total: int
    queued: int
    claimed: int
    completed: int
    failed: int
    oldest_queued_age_seconds: int | None
    statuses: list[McpJobStatusBucketResponse]
    tools: list[McpJobToolBucketResponse]


class OperationsSelfHostedMachineResponse(BaseModel):
    worker_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    name: str
    machine_id: str
    version: str
    trust_state: str
    worker_status: str
    runtime_status: str
    connection_status: str
    credential_status: str | None
    last_heartbeat_at: datetime | None
    heartbeat_age_seconds: int | None
    stale: bool
    active_job_claims: int
    active_mcp_jobs: int
    queued_mcp_jobs: int
    policy_summary: dict[str, object]
    capabilities: dict[str, object]
    warning_code: str | None = None
    warning_message: str | None = None
    remediation_actions: list[dict[str, object]] = Field(default_factory=list)


class OperationsSelfHostedMachinesResponse(BaseModel):
    generated_at: datetime
    total: int
    active: int
    degraded: int
    quarantined: int
    revoked: int
    offline: int
    stale: int
    active_job_claims: int
    active_mcp_jobs: int
    queued_mcp_jobs: int
    items: list[OperationsSelfHostedMachineResponse]


class OperationsControlPlaneIssueResponse(BaseModel):
    severity: str
    code: str
    message: str
    count: int = 1
    metadata: dict[str, object] = Field(default_factory=dict)


class OperationsControlPlaneResponse(BaseModel):
    generated_at: datetime
    health: str
    queue: QueueLatencyResponse
    worker_capacity: WorkerCapacityAggregateResponse
    runtime_capacity: OperationsRuntimeCapacityResponse
    scheduler: OperationsSchedulerResponse
    outcomes: OperationsOutcomesResponse
    mcp_jobs: OperationsMcpJobsResponse
    self_hosted_machines: OperationsSelfHostedMachinesResponse
    issues: list[OperationsControlPlaneIssueResponse]


class AuditEventFilterResponse(BaseModel):
    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


class RunEventFilterResponse(BaseModel):
    items: list[RunEventResponse]
    total: int
    limit: int
    offset: int


class SecurityEventFilterResponse(BaseModel):
    items: list[SecurityEventResponse]
    total: int
    limit: int
    offset: int
