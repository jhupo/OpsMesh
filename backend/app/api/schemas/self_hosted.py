from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, model_validator

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class EnrollmentTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    expires_at: datetime | None = None


class EnrollmentTokenCreateResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    status: str
    expires_at: datetime | None
    used_at: datetime | None
    token: str


class RuntimeRegistrationRequest(BaseModel):
    enrollment_token: str = Field(min_length=24)
    name: str = Field(min_length=1, max_length=160)
    machine_id: str = Field(min_length=1, max_length=160)
    version: str = Field(default="", max_length=80)
    capabilities: dict[str, object] = Field(default_factory=dict)
    attestation: dict[str, object] | None = None


class RuntimeRegistrationResponse(BaseModel):
    workspace_id: UUID
    workspace_runtime_id: UUID
    worker_id: UUID
    credential_token: str
    capability_attestation_state: str
    host_isolation_verified: bool


class WorkerHeartbeatRequest(BaseModel):
    status: str = Field(default="online", max_length=32)
    capabilities: dict[str, object] = Field(default_factory=dict)
    attestation: dict[str, object] | None = None


class WorkerHeartbeatResponse(BaseModel):
    worker_id: UUID
    workspace_runtime_id: UUID
    status: str
    last_heartbeat_at: datetime
    capability_attestation_state: str
    host_isolation_verified: bool


class SelfHostedWorkerCleanupResponse(BaseModel):
    degraded: int
    quarantined: int
    marked_offline: int = 0
    expired_job_claims: int = 0
    expired_mcp_jobs: int = 0


class SelfHostedWorkerControlRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class SelfHostedWorkerControlResponse(BaseModel):
    worker_id: UUID
    workspace_runtime_id: UUID
    action: str
    worker_status: str
    runtime_status: str
    connection_status: str
    affected_claims: int
    affected_runs: int


class SelfHostedWorkerTrustResponse(BaseModel):
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
    credential_last_used_at: datetime | None
    credential_revoked_at: datetime | None
    policy_summary: dict[str, object]
    policy_diagnostics: list[dict[str, object]]
    capabilities: dict[str, object]

    @field_serializer(
        "policy_summary",
        "capabilities",
        "capability_attestation_metadata",
    )
    def _serialize_worker_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("policy_diagnostics")
    def _serialize_policy_diagnostics(
        self,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [redact_sensitive_payload(item) for item in value]


class SelfHostedConnectorManifestResponse(BaseModel):
    workspace_id: UUID
    api_prefix: str
    connector: dict[str, object]
    endpoints: dict[str, object]
    capability_contract: dict[str, object]
    security: dict[str, object]
    version_policy: dict[str, object]

    @field_serializer(
        "connector",
        "endpoints",
        "capability_contract",
        "security",
        "version_policy",
    )
    def _serialize_manifest_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class RuntimeCredentialRevokeRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class SelfHostedJobResponse(BaseModel):
    agent_run_id: UUID
    task_id: UUID | None
    input: dict[str, object]
    model: str | None
    created_at: datetime


class SelfHostedProjectContractResponse(BaseModel):
    root_path: str
    snapshot_id: UUID
    fingerprint_sha256: str
    manifest: dict[str, object]
    archive_path: str
    output_upload_path_template: str

    @field_serializer("manifest")
    def _serialize_manifest(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class SelfHostedMcpJobResponse(BaseModel):
    id: UUID
    agent_run_id: UUID
    mcp_server_id: UUID
    tool_name: str
    request_payload: dict[str, object]
    created_at: datetime


class JobClaimResponse(BaseModel):
    claim_id: UUID
    agent_run_id: UUID
    status: str
    claimed_at: datetime
    project: SelfHostedProjectContractResponse | None


class JobCompleteRequest(BaseModel):
    status: str = Field(pattern="^(completed|failed)$")
    output: dict[str, object] | None = None
    error: dict[str, object] | None = None


class JobCompleteResponse(BaseModel):
    claim_id: UUID
    agent_run_id: UUID
    status: str
    completed_at: datetime


class McpJobClaimResponse(BaseModel):
    id: UUID
    status: str
    claimed_at: datetime


class McpJobCompleteRequest(BaseModel):
    status: str = Field(pattern="^(completed|failed)$")
    response_payload: dict[str, object] | None = None
    error_payload: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "McpJobCompleteRequest":
        if self.status == "completed":
            if self.response_payload is None or self.error_payload is not None:
                raise ValueError("Completed MCP jobs require a response without an error")
        elif self.error_payload is None or self.response_payload is not None:
            raise ValueError("Failed MCP jobs require an error without a response")
        return self


class McpJobCompleteResponse(BaseModel):
    id: UUID
    status: str
    completed_at: datetime


class ProgressEventRequest(BaseModel):
    agent_run_id: UUID
    event_type: str = Field(min_length=1, max_length=120)
    message: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class LocalFileReferenceRequest(BaseModel):
    task_id: UUID | None = None
    path: str = Field(min_length=1, max_length=1024)
    label: str = Field(default="", max_length=240)
    metadata: dict[str, object] = Field(default_factory=dict)


class LocalFileReferenceResponse(TimestampedModel):
    workspace_id: UUID
    workspace_runtime_id: UUID
    task_id: UUID | None
    path: str
    label: str
    file_metadata: dict[str, object]
    status: str

    @field_serializer("file_metadata")
    def _serialize_file_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class ArtifactUploadRequest(BaseModel):
    agent_run_id: UUID | None = None
    filename: str = Field(min_length=1, max_length=260)
    storage_key: str | None = Field(default=None, max_length=1024)
    checksum_sha256: str | None = Field(default=None, max_length=64)
    metadata: dict[str, object] = Field(default_factory=dict)


class ArtifactUploadResponse(TimestampedModel):
    workspace_id: UUID
    worker_id: UUID
    agent_run_id: UUID | None
    filename: str
    storage_key: str | None
    checksum_sha256: str | None
    artifact_metadata: dict[str, object]
    status: str

    @field_serializer("artifact_metadata")
    def _serialize_artifact_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
