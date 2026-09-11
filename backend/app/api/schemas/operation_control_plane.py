from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.operation_capacity import (
    OperationsRuntimeCapacityResponse,
    WorkerCapacityAggregateResponse,
)
from backend.app.api.schemas.operation_outcomes import (
    OperationsMcpJobsResponse,
    OperationsOutcomesResponse,
)
from backend.app.api.schemas.operation_queue import QueueLatencyResponse, QueueMetricsResponse
from backend.app.api.schemas.operation_scheduler import OperationsSchedulerResponse
from backend.app.api.schemas.redaction import redact_sensitive_payload


class OperationsOverviewResponse(BaseModel):
    queue: QueueMetricsResponse
    failed_runs: int
    offline_runtimes: int
    workers_online: int
    security_warnings: int
    data_lifecycle: dict[str, object] = Field(default_factory=dict)

    @field_serializer("data_lifecycle")
    def _serialize_data_lifecycle(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RunActivityOldestRunResponse(BaseModel):
    run_id: UUID
    task_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    runtime_id: UUID | None
    runtime_space_id: UUID | None
    status: str
    latest_event_type: str | None
    age_seconds: int
    started_at: datetime | None
    last_activity_at: datetime


class RunActivityPhaseBucketResponse(BaseModel):
    phase: str
    label: str
    count: int
    oldest_age_seconds: int | None
    oldest_run: RunActivityOldestRunResponse | None = None
    recommended_action: str | None = None


class OperationsRunActivityResponse(BaseModel):
    generated_at: datetime
    team_id: UUID | None = None
    total_active_runs: int
    scanned_active_runs: int
    truncated: bool
    status_counts: dict[str, int]
    phases: list[RunActivityPhaseBucketResponse]
    oldest_active_run: RunActivityOldestRunResponse | None = None


class OperationsSelfHostedMachineResponse(BaseModel):
    worker_id: UUID
    workspace_runtime_id: UUID
    runtime_space_id: UUID | None
    name: str
    machine_id: str
    version: str
    trust_state: str
    capability_attestation_state: str
    capability_attestation_fingerprint: str | None
    capability_attestation_metadata: dict[str, object]
    capability_attested_at: datetime | None
    host_isolation_verified: bool
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

    @field_serializer(
        "policy_summary",
        "capabilities",
        "capability_attestation_metadata",
    )
    def _serialize_machine_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("remediation_actions")
    def _serialize_remediation_actions(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


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

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


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
