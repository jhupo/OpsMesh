from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.utils import age_seconds, positive_int_or_none, string_list
from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.operations.contracts.control_plane import (
    OperationsSelfHostedMachineResponse,
    OperationsSelfHostedMachinesResponse,
)
from backend.app.runtime.self_hosted.enrollment.trust import (
    worker_capability_attestation_state,
    worker_host_isolation_verified,
    worker_trust_state,
)
from backend.app.runtime.self_hosted.models import (
    RuntimeCredential,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)


def self_hosted_policy_summary(capabilities: dict[str, object]) -> dict[str, object]:
    return {
        "allowed_tools": string_list(capabilities.get("allowed_tools")),
        "supported_models": string_list(capabilities.get("supported_models")),
        "supported_runtimes": string_list(capabilities.get("supported_runtimes")),
        "supported_network_modes": string_list(capabilities.get("supported_network_modes")),
        "allowed_runtime_space_ids": string_list(capabilities.get("allowed_runtime_space_ids")),
        "max_concurrent_jobs": positive_int_or_none(capabilities.get("max_concurrent_jobs")),
        "max_concurrent_mcp_jobs": positive_int_or_none(
            capabilities.get("max_concurrent_mcp_jobs")
        ),
        "max_artifact_bytes": positive_int_or_none(capabilities.get("max_artifact_bytes")),
    }





def self_hosted_machine_warning(
    trust_state: str,
    *,
    stale: bool,
) -> tuple[str | None, str | None]:
    if trust_state == "revoked":
        return "credential_revoked", "Machine credential is revoked."
    if trust_state == "quarantined":
        return "machine_quarantined", "Machine is quarantined and cannot accept jobs."
    if trust_state == "degraded":
        return "machine_degraded", "Machine is degraded and cannot accept jobs."
    if trust_state == "offline":
        return "machine_offline", "Machine is offline."
    if stale:
        return "heartbeat_stale", "Machine heartbeat is stale."
    return None, None


def self_hosted_remediation_actions(
    trust_state: str,
    *,
    stale: bool,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if stale:
        actions.append(
            {
                "code": "check_runner_heartbeat",
                "label": "Check runner heartbeat",
                "severity": "warning",
                "description": (
                    "Verify the self-hosted runner process is online and can reach the API."
                ),
            }
        )
    if trust_state == "degraded":
        actions.extend(
            [
                {
                    "code": "restart_runner",
                    "label": "Restart runner",
                    "severity": "warning",
                    "description": (
                        "Restart the local worker and confirm its policy capabilities match "
                        "the workspace."
                    ),
                },
                {
                    "code": "review_machine_policy",
                    "label": "Review machine policy",
                    "severity": "warning",
                    "description": (
                        "Check allowed tools, runtime spaces, network modes, and capacity "
                        "limits."
                    ),
                },
            ]
        )
    elif trust_state == "quarantined":
        actions.append(
            {
                "code": "review_quarantine_reason",
                "label": "Review quarantine",
                "severity": "critical",
                "description": (
                    "Inspect security events and only restore the runner after the issue is "
                    "resolved."
                ),
            }
        )
    elif trust_state == "revoked":
        actions.append(
            {
                "code": "rotate_runtime_credential",
                "label": "Rotate credential",
                "severity": "critical",
                "description": "Issue a new runtime credential and re-enroll the local machine.",
            }
        )
    elif trust_state == "offline":
        actions.append(
            {
                "code": "start_runner",
                "label": "Start runner",
                "severity": "warning",
                "description": (
                    "Start the local worker service or reconnect the machine to the network."
                ),
            }
        )
    return actions







@dataclass(frozen=True, slots=True)
class SelfHostedMachineRecords:
    workers: Sequence[SelfHostedWorker]
    runtimes: dict[UUID, WorkspaceRuntime]
    credentials: dict[UUID, RuntimeCredential]
    active_claim_counts: dict[UUID, int]
    mcp_claim_counts: dict[UUID, int]
    queued_mcp_counts: dict[UUID, int]


class SelfHostedMachineRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_workspace_records(self, workspace_id: UUID) -> SelfHostedMachineRecords:
        workers = self._session.scalars(
            select(SelfHostedWorker)
            .where(SelfHostedWorker.workspace_id == workspace_id)
            .order_by(SelfHostedWorker.updated_at.desc(), SelfHostedWorker.name.asc())
        ).all()
        if not workers:
            return SelfHostedMachineRecords(
                workers=[],
                runtimes={},
                credentials={},
                active_claim_counts={},
                mcp_claim_counts={},
                queued_mcp_counts={},
            )

        runtime_ids = [worker.workspace_runtime_id for worker in workers]
        worker_ids = [worker.id for worker in workers]
        return SelfHostedMachineRecords(
            workers=workers,
            runtimes=self._workspace_runtimes(workspace_id, runtime_ids),
            credentials=self._runtime_credentials(workspace_id, runtime_ids),
            active_claim_counts=self._active_job_claims(workspace_id, worker_ids),
            mcp_claim_counts=self._claimed_mcp_jobs(workspace_id, worker_ids),
            queued_mcp_counts=self._queued_mcp_jobs(workspace_id, runtime_ids),
        )

    def _workspace_runtimes(
        self,
        workspace_id: UUID,
        runtime_ids: list[UUID],
    ) -> dict[UUID, WorkspaceRuntime]:
        return {
            runtime.id: runtime
            for runtime in self._session.scalars(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.id.in_(runtime_ids),
                )
            ).all()
        }

    def _runtime_credentials(
        self,
        workspace_id: UUID,
        runtime_ids: list[UUID],
    ) -> dict[UUID, RuntimeCredential]:
        return {
            credential.workspace_runtime_id: credential
            for credential in self._session.scalars(
                select(RuntimeCredential)
                .where(
                    RuntimeCredential.workspace_id == workspace_id,
                    RuntimeCredential.workspace_runtime_id.in_(runtime_ids),
                )
                .order_by(RuntimeCredential.created_at.asc())
            ).all()
        }

    def _active_job_claims(
        self,
        workspace_id: UUID,
        worker_ids: list[UUID],
    ) -> dict[UUID, int]:
        rows = (
            self._session.execute(
                select(SelfHostedJobClaim.worker_id, func.count())
                .where(
                    SelfHostedJobClaim.workspace_id == workspace_id,
                    SelfHostedJobClaim.worker_id.in_(worker_ids),
                    SelfHostedJobClaim.status == "claimed",
                )
                .group_by(SelfHostedJobClaim.worker_id)
            )
            .tuples()
            .all()
        )
        return _counts_by_uuid(rows)

    def _claimed_mcp_jobs(
        self,
        workspace_id: UUID,
        worker_ids: list[UUID],
    ) -> dict[UUID, int]:
        rows = (
            self._session.execute(
                select(SelfHostedMcpJob.worker_id, func.count())
                .where(
                    SelfHostedMcpJob.workspace_id == workspace_id,
                    SelfHostedMcpJob.worker_id.in_(worker_ids),
                    SelfHostedMcpJob.status == "claimed",
                )
                .group_by(SelfHostedMcpJob.worker_id)
            )
            .tuples()
            .all()
        )
        return _counts_by_uuid(rows)

    def _queued_mcp_jobs(
        self,
        workspace_id: UUID,
        runtime_ids: list[UUID],
    ) -> dict[UUID, int]:
        rows = (
            self._session.execute(
                select(SelfHostedMcpJob.workspace_runtime_id, func.count())
                .where(
                    SelfHostedMcpJob.workspace_id == workspace_id,
                    SelfHostedMcpJob.workspace_runtime_id.in_(runtime_ids),
                    SelfHostedMcpJob.status == "queued",
                )
                .group_by(SelfHostedMcpJob.workspace_runtime_id)
            )
            .tuples()
            .all()
        )
        return _counts_by_uuid(rows)


def _counts_by_uuid(rows: Sequence[tuple[UUID | None, int]]) -> dict[UUID, int]:
    return {key: int(count) for key, count in rows if key is not None}







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
    trust_state = worker_trust_state(worker, runtime, credential)
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
