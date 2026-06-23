from backend.app.api.schemas.self_hosted import (
    EnrollmentTokenCreateResponse,
    RuntimeRegistrationResponse,
    SelfHostedWorkerControlResponse,
    SelfHostedWorkerTrustResponse,
    WorkerHeartbeatResponse,
)
from backend.app.self_hosted.models import SelfHostedWorker
from backend.app.self_hosted.trust import WorkerTrustSnapshot
from backend.app.self_hosted.types import (
    RegisteredRuntime,
    WorkerControlResult,
)


def enrollment_token_response(created) -> EnrollmentTokenCreateResponse:
    return EnrollmentTokenCreateResponse(
        id=created.record.id,
        created_at=created.record.created_at,
        updated_at=created.record.updated_at,
        workspace_id=created.record.workspace_id,
        name=created.record.name,
        status=created.record.status,
        expires_at=created.record.expires_at,
        used_at=created.record.used_at,
        token=created.token,
    )


def runtime_registration_response(
    registered: RegisteredRuntime,
) -> RuntimeRegistrationResponse:
    return RuntimeRegistrationResponse(
        workspace_id=registered.workspace_id,
        workspace_runtime_id=registered.workspace_runtime_id,
        worker_id=registered.worker_id,
        credential_token=registered.credential_token,
    )


def heartbeat_response(worker: SelfHostedWorker) -> WorkerHeartbeatResponse:
    return WorkerHeartbeatResponse(
        worker_id=worker.id,
        workspace_runtime_id=worker.workspace_runtime_id,
        status=worker.status,
        last_heartbeat_at=worker.last_heartbeat_at,
    )


def worker_control_response(result: WorkerControlResult) -> SelfHostedWorkerControlResponse:
    return SelfHostedWorkerControlResponse(
        worker_id=result.worker.id,
        workspace_runtime_id=result.runtime.id,
        action=result.action,
        worker_status=result.worker.status,
        runtime_status=result.runtime.status,
        connection_status=result.runtime.connection_status,
        affected_claims=result.affected_claims,
        affected_runs=result.affected_runs,
    )


def worker_trust_response(snapshot: WorkerTrustSnapshot) -> SelfHostedWorkerTrustResponse:
    credential = snapshot.credential
    return SelfHostedWorkerTrustResponse(
        worker_id=snapshot.worker.id,
        workspace_runtime_id=snapshot.worker.workspace_runtime_id,
        runtime_space_id=snapshot.runtime.runtime_space_id,
        name=snapshot.worker.name,
        machine_id=snapshot.worker.machine_id,
        version=snapshot.worker.version,
        trust_state=snapshot.trust_state,
        worker_status=snapshot.worker.status,
        runtime_status=snapshot.runtime.status,
        connection_status=snapshot.runtime.connection_status,
        credential_status=credential.status if credential else None,
        last_heartbeat_at=snapshot.worker.last_heartbeat_at,
        credential_last_used_at=credential.last_used_at if credential else None,
        credential_revoked_at=credential.revoked_at if credential else None,
        policy_summary=snapshot.policy_summary,
        policy_diagnostics=snapshot.policy_diagnostics,
        capabilities=snapshot.worker.capabilities,
    )
