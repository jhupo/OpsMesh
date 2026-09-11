from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_control_plane import (
    OperationsSelfHostedMachineResponse,
    OperationsSelfHostedMachinesResponse,
)
from backend.app.operations.self_hosted_machine_health import (
    self_hosted_machine_warning,
    self_hosted_remediation_actions,
    self_hosted_trust_state,
)
from backend.app.operations.self_hosted_machine_policy import self_hosted_policy_summary
from backend.app.operations.self_hosted_machine_repository import (
    SelfHostedMachineRecords,
    SelfHostedMachineRepository,
)
from backend.app.operations.utils import age_seconds
from backend.app.self_hosted.models import SelfHostedWorker
from backend.app.self_hosted.trust import (
    worker_capability_attestation_state,
    worker_host_isolation_verified,
)


class OperationsSelfHostedMachineService:
    def __init__(self, session: Session) -> None:
        self._repository = SelfHostedMachineRepository(session)

    def self_hosted_machines_payload(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
    ) -> OperationsSelfHostedMachinesResponse:
        now = datetime.now(UTC)
        records = self._repository.list_workspace_records(workspace_id)
        if not records.workers:
            return _empty_self_hosted_machines(now)

        items: list[OperationsSelfHostedMachineResponse] = []
        state_counts = _empty_state_counts()
        stale_count = 0
        for worker in records.workers:
            item = _machine_response(
                worker,
                records,
                now=now,
                stale_after_seconds=stale_after_seconds,
            )
            if item is None:
                continue
            items.append(item)
            state_counts[item.trust_state] = state_counts.get(item.trust_state, 0) + 1
            stale_count += 1 if item.stale else 0

        return OperationsSelfHostedMachinesResponse(
            generated_at=now,
            total=len(items),
            stale=stale_count,
            active_job_claims=sum(item.active_job_claims for item in items),
            active_mcp_jobs=sum(item.active_mcp_jobs for item in items),
            queued_mcp_jobs=sum(item.queued_mcp_jobs for item in items),
            items=items,
            **state_counts,
        )


def _machine_response(
    worker: SelfHostedWorker,
    records: SelfHostedMachineRecords,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> OperationsSelfHostedMachineResponse | None:
    runtime = records.runtimes.get(worker.workspace_runtime_id)
    if runtime is None:
        return None

    credential = records.credentials.get(runtime.id)
    trust_state = self_hosted_trust_state(worker, runtime, credential)
    capability_attestation_state = worker_capability_attestation_state(
        worker,
        runtime,
        credential,
    )
    heartbeat_age_seconds = age_seconds(now, worker.last_heartbeat_at)
    stale = _is_stale_machine(heartbeat_age_seconds, stale_after_seconds, trust_state)
    warning_code, warning_message = self_hosted_machine_warning(
        trust_state,
        stale=stale,
    )
    return OperationsSelfHostedMachineResponse(
        worker_id=worker.id,
        workspace_runtime_id=runtime.id,
        runtime_space_id=runtime.runtime_space_id,
        name=worker.name,
        machine_id=worker.machine_id,
        version=worker.version,
        trust_state=trust_state,
        capability_attestation_state=capability_attestation_state,
        capability_attestation_fingerprint=worker.capability_attestation_fingerprint,
        capability_attestation_metadata=dict(worker.capability_attestation_metadata or {}),
        capability_attested_at=worker.capability_attested_at,
        host_isolation_verified=worker_host_isolation_verified(worker, runtime, credential),
        worker_status=worker.status,
        runtime_status=runtime.status,
        connection_status=runtime.connection_status,
        credential_status=credential.status if credential else None,
        last_heartbeat_at=worker.last_heartbeat_at,
        heartbeat_age_seconds=heartbeat_age_seconds,
        stale=stale,
        active_job_claims=records.active_claim_counts.get(worker.id, 0),
        active_mcp_jobs=records.mcp_claim_counts.get(worker.id, 0),
        queued_mcp_jobs=records.queued_mcp_counts.get(runtime.id, 0),
        policy_summary=self_hosted_policy_summary(worker.capabilities),
        capabilities=worker.capabilities,
        warning_code=warning_code,
        warning_message=warning_message,
        remediation_actions=self_hosted_remediation_actions(
            trust_state,
            stale=stale,
        ),
    )


def _is_stale_machine(
    heartbeat_age_seconds: int | None,
    stale_after_seconds: int,
    trust_state: str,
) -> bool:
    return (
        heartbeat_age_seconds is not None
        and heartbeat_age_seconds >= stale_after_seconds
        and trust_state in {"active", "degraded", "offline"}
    )


def _empty_state_counts() -> dict[str, int]:
    return {"active": 0, "degraded": 0, "quarantined": 0, "revoked": 0, "offline": 0}


def _empty_self_hosted_machines(now: datetime) -> OperationsSelfHostedMachinesResponse:
    return OperationsSelfHostedMachinesResponse(
        generated_at=now,
        total=0,
        active=0,
        degraded=0,
        quarantined=0,
        revoked=0,
        offline=0,
        stale=0,
        active_job_claims=0,
        active_mcp_jobs=0,
        queued_mcp_jobs=0,
        items=[],
    )
