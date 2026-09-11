from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.self_hosted.models import (
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
