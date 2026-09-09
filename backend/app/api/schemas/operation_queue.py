from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload
from backend.app.api.schemas.runs import AgentRunResponse
from backend.app.workers.jobs import JobPayload


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


StaleRunRecoverStatus = Literal["queued", "running", "waiting_runtime"]


class StaleRunDiagnosticResponse(BaseModel):
    run_id: UUID
    status: StaleRunRecoverStatus
    stale_reason_code: str
    stale_reason_message: str
    age_seconds: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    task_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    runtime_id: UUID | None
    runtime_space_id: UUID | None
    worker_id: str | None = None
    worker_lease_status: str | None = None
    worker_lease_started_at: datetime | None = None
    worker_lease_age_seconds: int | None = None


class StaleRunsDiagnosticsResponse(BaseModel):
    generated_at: datetime
    stale_after_seconds: int
    total: int
    items: list[StaleRunDiagnosticResponse]


class StaleRunRecoveryRequest(BaseModel):
    stale_after_seconds: int = Field(default=900, ge=60, le=86_400)
    statuses: list[StaleRunRecoverStatus] = Field(
        default=["queued", "running", "waiting_runtime"],
        min_length=1,
        max_length=3,
    )
    limit: int = Field(default=100, ge=1, le=500)
    queue_name: str = Field(default="agent_runs", min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=240)


class StaleRunRecoveryItemResponse(BaseModel):
    run_id: UUID
    previous_status: StaleRunRecoverStatus
    action: Literal["requeued", "failed_closed"]
    enqueued: bool = False


class StaleRunRecoveryResponse(BaseModel):
    workspace_id: UUID
    stale_after_seconds: int
    scanned_runs: int
    requeued_runs: int
    failed_closed_runs: int
    expired_worker_leases: int
    items: list[StaleRunRecoveryItemResponse]


class FailedJobInspectionResponse(BaseModel):
    runs: list[AgentRunResponse]
    total: int


class QueueLatencyResponse(BaseModel):
    queue_name: str
    queued: int
    oldest_age_seconds: int | None
    newest_age_seconds: int | None
    average_age_seconds: int | None
    highest_priority: int | None


class QueuePriorityBucketResponse(BaseModel):
    priority: int
    queued: int
    dead_letter: int
    oldest_queued_age_seconds: int | None


class QueueJobTypeBucketResponse(BaseModel):
    job_type: str
    queued: int
    dead_letter: int
    highest_priority: int | None
    oldest_queued_age_seconds: int | None


class OperationsQueueInsightsResponse(BaseModel):
    generated_at: datetime
    queue_name: str
    scan_limit: int
    queued_total: int
    dead_letter_total: int
    queued_scanned: int
    dead_letter_scanned: int
    truncated: bool
    oldest_queued_age_seconds: int | None
    highest_priority: int | None
    priority_buckets: list[QueuePriorityBucketResponse]
    job_type_buckets: list[QueueJobTypeBucketResponse]


QueueGovernanceReconcileAction = Literal[
    "requeue_missing_runs",
    "remove_orphaned_jobs",
    "remove_non_runnable_jobs",
]


class QueueGovernanceIssueResponse(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    message: str
    count: int
    resource_ids: list[UUID] = Field(default_factory=list)
    job_ids: list[UUID] = Field(default_factory=list)
    oldest_age_seconds: int | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class QueueGovernanceDiagnosticsResponse(BaseModel):
    generated_at: datetime
    queue_name: str
    scan_limit: int
    stale_after_seconds: int
    queued_total: int
    queued_scanned: int
    agent_run_jobs_scanned: int
    dead_letter_total: int
    orphaned_queue_jobs: int
    non_runnable_queue_jobs: int
    duplicate_queue_jobs: int
    queued_runs_missing_queue_job: int
    old_queued_jobs: int
    truncated: bool
    issues: list[QueueGovernanceIssueResponse]
    recommended_actions: list[QueueGovernanceReconcileAction]


class QueueGovernanceReconcileRequest(BaseModel):
    queue_name: str = Field(default="agent_runs", min_length=1, max_length=120)
    scan_limit: int = Field(default=500, ge=1, le=5_000)
    stale_after_seconds: int = Field(default=900, ge=60, le=86_400)
    actions: list[QueueGovernanceReconcileAction] = Field(min_length=1, max_length=3)
    max_items: int = Field(default=100, ge=1, le=500)
    reason: str | None = Field(default=None, max_length=240)


class QueueGovernanceReconcileResponse(BaseModel):
    workspace_id: UUID
    queue_name: str
    scanned_jobs: int
    actions: list[QueueGovernanceReconcileAction]
    requeued_missing_runs: int
    removed_orphaned_jobs: int
    removed_non_runnable_jobs: int
    skipped_items: int
    remaining_issues: list[QueueGovernanceIssueResponse]
