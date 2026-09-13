from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.self_hosted.models import (
    RuntimeCredential,
    RuntimeEnrollmentToken,
    SelfHostedWorker,
)


@dataclass(frozen=True)
class CreatedEnrollmentToken:
    record: RuntimeEnrollmentToken
    token: str


@dataclass(frozen=True)
class RegisteredRuntime:
    workspace_id: UUID
    workspace_runtime_id: UUID
    worker_id: UUID
    credential_token: str
    capability_attestation_state: str
    host_isolation_verified: bool


@dataclass(frozen=True)
class AuthenticatedWorker:
    worker: SelfHostedWorker
    runtime: WorkspaceRuntime
    credential: RuntimeCredential


@dataclass(frozen=True)
class WorkerTrustCleanupResult:
    degraded: int = 0
    quarantined: int = 0
    expired_job_claims: int = 0
    expired_mcp_jobs: int = 0


@dataclass(frozen=True)
class WorkerControlResult:
    worker: SelfHostedWorker
    runtime: WorkspaceRuntime
    action: str
    affected_claims: int
    affected_runs: int


class EnrollmentTokenCreatePayload(Protocol):
    name: str
    expires_at: datetime | None


class RuntimeRegistrationPayload(Protocol):
    enrollment_token: str
    name: str
    machine_id: str
    version: str
    capabilities: dict[str, object]
    attestation: dict[str, object] | None


class WorkerHeartbeatPayload(Protocol):
    status: str
    capabilities: dict[str, object]
    attestation: dict[str, object] | None


class LocalFileReferencePayload(Protocol):
    task_id: UUID | None
    path: str
    label: str
    metadata: dict[str, object]


class ArtifactUploadPayload(Protocol):
    agent_run_id: UUID | None
    filename: str
    storage_key: str | None
    checksum_sha256: str | None
    metadata: dict[str, object]


class JobCompletePayload(Protocol):
    status: str
    output: dict[str, object] | None
    error: dict[str, object] | None


class McpJobCompletePayload(Protocol):
    status: str
    response_payload: dict[str, object] | None
    error_payload: dict[str, object] | None


class ProgressEventPayload(Protocol):
    agent_run_id: UUID
    event_type: str
    message: str
    metadata: dict[str, object]
