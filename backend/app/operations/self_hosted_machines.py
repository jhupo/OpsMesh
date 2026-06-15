from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsSelfHostedMachineResponse,
    OperationsSelfHostedMachinesResponse,
)
from backend.app.core.typing import string_list
from backend.app.operations.utils import age_seconds, positive_int_or_none
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.models import (
    RuntimeCredential,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)


class OperationsSelfHostedMachineService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def self_hosted_machines_payload(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
    ) -> OperationsSelfHostedMachinesResponse:
        now = datetime.now(UTC)
        workers = self._session.scalars(
            select(SelfHostedWorker)
            .where(SelfHostedWorker.workspace_id == workspace_id)
            .order_by(SelfHostedWorker.updated_at.desc(), SelfHostedWorker.name.asc())
        ).all()
        if not workers:
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
        runtime_ids = [worker.workspace_runtime_id for worker in workers]
        worker_ids = [worker.id for worker in workers]
        runtimes = {
            runtime.id: runtime
            for runtime in self._session.scalars(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.id.in_(runtime_ids),
                )
            ).all()
        }
        credentials = {
            row.workspace_runtime_id: row
            for row in self._session.scalars(
                select(RuntimeCredential)
                .where(
                    RuntimeCredential.workspace_id == workspace_id,
                    RuntimeCredential.workspace_runtime_id.in_(runtime_ids),
                )
                .order_by(RuntimeCredential.created_at.asc())
            ).all()
        }
        active_claim_counts = _counts_by_uuid(
            self._session.execute(
                select(SelfHostedJobClaim.worker_id, func.count())
                .where(
                    SelfHostedJobClaim.workspace_id == workspace_id,
                    SelfHostedJobClaim.worker_id.in_(worker_ids),
                    SelfHostedJobClaim.status == "claimed",
                )
                .group_by(SelfHostedJobClaim.worker_id)
            ).all()
        )
        mcp_claim_counts = _counts_by_uuid(
            self._session.execute(
                select(SelfHostedMcpJob.worker_id, func.count())
                .where(
                    SelfHostedMcpJob.workspace_id == workspace_id,
                    SelfHostedMcpJob.worker_id.in_(worker_ids),
                    SelfHostedMcpJob.status == "claimed",
                )
                .group_by(SelfHostedMcpJob.worker_id)
            ).all()
        )
        queued_mcp_counts = _counts_by_uuid(
            self._session.execute(
                select(SelfHostedMcpJob.workspace_runtime_id, func.count())
                .where(
                    SelfHostedMcpJob.workspace_id == workspace_id,
                    SelfHostedMcpJob.workspace_runtime_id.in_(runtime_ids),
                    SelfHostedMcpJob.status == "queued",
                )
                .group_by(SelfHostedMcpJob.workspace_runtime_id)
            ).all()
        )
        state_counts = {"active": 0, "degraded": 0, "quarantined": 0, "revoked": 0, "offline": 0}
        items: list[OperationsSelfHostedMachineResponse] = []
        stale_count = 0
        for worker in workers:
            runtime = runtimes.get(worker.workspace_runtime_id)
            if runtime is None:
                continue
            credential = credentials.get(runtime.id)
            trust_state = _self_hosted_trust_state(worker, runtime, credential)
            state_counts[trust_state] = state_counts.get(trust_state, 0) + 1
            heartbeat_age_seconds = age_seconds(now, worker.last_heartbeat_at)
            stale = (
                heartbeat_age_seconds is not None
                and heartbeat_age_seconds >= stale_after_seconds
                and trust_state in {"active", "degraded", "offline"}
            )
            stale_count += 1 if stale else 0
            warning_code, warning_message = _self_hosted_machine_warning(
                trust_state,
                stale=stale,
            )
            items.append(
                OperationsSelfHostedMachineResponse(
                    worker_id=worker.id,
                    workspace_runtime_id=runtime.id,
                    runtime_space_id=runtime.runtime_space_id,
                    name=worker.name,
                    machine_id=worker.machine_id,
                    version=worker.version,
                    trust_state=trust_state,
                    worker_status=worker.status,
                    runtime_status=runtime.status,
                    connection_status=runtime.connection_status,
                    credential_status=credential.status if credential else None,
                    last_heartbeat_at=worker.last_heartbeat_at,
                    heartbeat_age_seconds=heartbeat_age_seconds,
                    stale=stale,
                    active_job_claims=active_claim_counts.get(worker.id, 0),
                    active_mcp_jobs=mcp_claim_counts.get(worker.id, 0),
                    queued_mcp_jobs=queued_mcp_counts.get(runtime.id, 0),
                    policy_summary=_self_hosted_policy_summary(worker.capabilities),
                    capabilities=worker.capabilities,
                    warning_code=warning_code,
                    warning_message=warning_message,
                    remediation_actions=_self_hosted_remediation_actions(
                        trust_state,
                        stale=stale,
                    ),
                )
            )
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


def _counts_by_uuid(rows: list[tuple[UUID | None, int]]) -> dict[UUID, int]:
    return {key: int(count) for key, count in rows if key is not None}


def _self_hosted_trust_state(
    worker: SelfHostedWorker,
    runtime: WorkspaceRuntime,
    credential: RuntimeCredential | None,
) -> str:
    if credential is not None and credential.status == "revoked":
        return "revoked"
    if worker.status == "revoked" or runtime.status == "revoked":
        return "revoked"
    if worker.status == "quarantined" or runtime.status == "quarantined":
        return "quarantined"
    if worker.status == "degraded" or runtime.connection_status == "degraded":
        return "degraded"
    if worker.status in {"offline", "disabled"} or runtime.connection_status == "offline":
        return "offline"
    return "active"


def _self_hosted_policy_summary(capabilities: dict[str, object]) -> dict[str, object]:
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


def _self_hosted_machine_warning(
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


def _self_hosted_remediation_actions(
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
        actions.append(
            {
                "code": "restart_runner",
                "label": "Restart runner",
                "severity": "warning",
                "description": (
                    "Restart the local worker and confirm its policy capabilities match the "
                    "workspace."
                ),
            }
        )
        actions.append(
            {
                "code": "review_machine_policy",
                "label": "Review machine policy",
                "severity": "warning",
                "description": (
                    "Check allowed tools, runtime spaces, network modes, and capacity limits."
                ),
            }
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
