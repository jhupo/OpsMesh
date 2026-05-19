from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import TimestampedModel


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


class RuntimeRegistrationResponse(BaseModel):
    workspace_id: UUID
    workspace_runtime_id: UUID
    worker_id: UUID
    credential_token: str


class WorkerHeartbeatRequest(BaseModel):
    status: str = Field(default="online", max_length=32)
    capabilities: dict[str, object] = Field(default_factory=dict)


class WorkerHeartbeatResponse(BaseModel):
    worker_id: UUID
    workspace_runtime_id: UUID
    status: str
    last_heartbeat_at: datetime


class SelfHostedWorkerCleanupResponse(BaseModel):
    marked_offline: int


class SelfHostedJobResponse(BaseModel):
    agent_run_id: UUID
    task_id: UUID | None
    input: dict[str, object]
    model: str | None
    created_at: datetime


class JobClaimResponse(BaseModel):
    claim_id: UUID
    agent_run_id: UUID
    status: str
    claimed_at: datetime


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
