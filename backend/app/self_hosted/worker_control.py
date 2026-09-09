from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.statuses import ACTIVE_RUN_STATUSES
from backend.app.runs.models import AgentRun
from backend.app.runs.service import RunStateService
from backend.app.runs.status import RunStatus
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.jobs import SelfHostedJobFinalizer, dt_iso
from backend.app.self_hosted.models import RuntimeCredential, SelfHostedWorker
from backend.app.self_hosted.types import WorkerControlResult


class SelfHostedWorkerControlService:
    def __init__(
        self,
        session: Session,
        events: SelfHostedEventRecorder,
        jobs: SelfHostedJobFinalizer,
    ) -> None:
        self._session = session
        self._events = events
        self._jobs = jobs

    def revoke_credential(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        *,
        actor_user_id: UUID | None = None,
        reason: str = "",
    ) -> RuntimeCredential | None:
        credential = self._session.get(RuntimeCredential, credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            return None
        now = datetime.now(UTC)
        credential.status = "revoked"
        credential.revoked_at = now
        runtime = self._session.get(WorkspaceRuntime, credential.workspace_runtime_id)
        worker = self._session.scalar(
            select(SelfHostedWorker).where(
                SelfHostedWorker.workspace_runtime_id == credential.workspace_runtime_id
            )
        )
        affected_claims = self._jobs.active_claims_for_worker(worker) if worker is not None else []
        affected_run_ids: list[str] = []
        for claim in affected_claims:
            claim.status = "revoked"
            claim.completed_at = now
            run = self._session.get(AgentRun, claim.agent_run_id)
            if run is None:
                continue
            affected_run_ids.append(str(run.id))
            if run.status in ACTIVE_RUN_STATUSES:
                RunStateService().transition(
                    run,
                    RunStatus.FAILED,
                    completed_at=now,
                    error={
                        "code": "runtime_credential_revoked",
                        "message": "Self-hosted runtime credential was revoked.",
                        "credential_id": str(credential.id),
                    },
                )
                self._events.append_run_event(
                    run,
                    "self_hosted.run_failed_by_revoke",
                    "Self-hosted runtime credential was revoked.",
                    {
                        "credential_id": str(credential.id),
                        "worker_id": str(worker.id) if worker else None,
                        "reason": reason,
                    },
                )
            self._jobs.release_run_reservations(run, released_at=now)
        if worker is not None:
            worker.status = "revoked"
        if runtime is not None:
            runtime.status = "revoked"
            runtime.connection_status = "offline"
            event_metadata: dict[str, object] = {
                "credential_id": str(credential.id),
                "worker_id": str(worker.id) if worker else None,
                "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
                "reason": reason,
                "affected_claim_ids": [str(claim.id) for claim in affected_claims],
                "affected_run_ids": affected_run_ids,
                "final_heartbeat": {
                    "worker_status": worker.status if worker is not None else None,
                    "worker_last_heartbeat_at": (
                        dt_iso(worker.last_heartbeat_at) if worker is not None else None
                    ),
                    "runtime_status": runtime.status,
                    "runtime_connection_status": runtime.connection_status,
                    "runtime_last_heartbeat_at": dt_iso(runtime.last_heartbeat_at),
                },
            }
            self._events.append_runtime_event(
                runtime,
                "self_hosted.credential_revoked",
                reason or str(credential.id),
                event_metadata,
            )
            self._events.append_runtime_space_event(
                runtime,
                "self_hosted.credential_revoked",
                str(credential.id),
                event_metadata,
            )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def control_worker(
        self,
        workspace_id: UUID,
        worker_id: UUID,
        *,
        action: str,
        actor_user_id: UUID | None = None,
        reason: str = "",
    ) -> WorkerControlResult | None:
        worker = self._session.get(SelfHostedWorker, worker_id)
        if worker is None or worker.workspace_id != workspace_id:
            return None
        runtime = self._session.get(WorkspaceRuntime, worker.workspace_runtime_id)
        if runtime is None or runtime.workspace_id != workspace_id:
            return None
        now = datetime.now(UTC)
        normalized_action = action.strip().lower()
        affected_claims = 0
        affected_runs = 0
        metadata: dict[str, object] = {
            "worker_id": str(worker.id),
            "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
            "reason": reason,
            "previous_worker_status": worker.status,
            "previous_runtime_status": runtime.status,
            "previous_connection_status": runtime.connection_status,
        }

        if normalized_action == "quarantine":
            worker.status = "quarantined"
            runtime.status = "quarantined"
            runtime.connection_status = "offline"
            affected_claims, affected_runs = self._jobs.close_active_claims_for_worker(
                worker,
                claim_status="quarantined",
                error_code="self_hosted_worker_quarantined",
                error_message="Self-hosted worker was quarantined by an operator.",
                event_type="self_hosted.run_failed_by_quarantine",
                event_message="Self-hosted worker was quarantined.",
                now=now,
                reason=reason,
            )
        elif normalized_action == "resume":
            if worker.status == "revoked" or runtime.status == "revoked":
                raise ValueError("Revoked self-hosted workers cannot be resumed")
            worker.status = "online"
            runtime.status = "active"
            runtime.connection_status = _connection_status_after_resume(
                worker.last_heartbeat_at,
                now,
            )
        elif normalized_action == "revoke":
            credential = self._jobs.latest_credential_for_runtime(runtime.id)
            if credential is not None and credential.status != "revoked":
                active_claims = self._jobs.active_claims_for_worker(worker)
                affected_claims = len(active_claims)
                affected_runs = len({claim.agent_run_id for claim in active_claims})
                revoked = self.revoke_credential(
                    workspace_id,
                    credential.id,
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
                if revoked is None:
                    return None
                worker = self._session.get(SelfHostedWorker, worker_id) or worker
                runtime = self._session.get(WorkspaceRuntime, runtime.id) or runtime
                return WorkerControlResult(
                    worker=worker,
                    runtime=runtime,
                    action=normalized_action,
                    affected_claims=affected_claims,
                    affected_runs=affected_runs,
                )
            worker.status = "revoked"
            runtime.status = "revoked"
            runtime.connection_status = "offline"
            affected_claims, affected_runs = self._jobs.close_active_claims_for_worker(
                worker,
                claim_status="revoked",
                error_code="self_hosted_worker_revoked",
                error_message="Self-hosted worker was revoked by an operator.",
                event_type="self_hosted.run_failed_by_revoke",
                event_message="Self-hosted worker was revoked.",
                now=now,
                reason=reason,
            )
        else:
            raise ValueError("Unsupported self-hosted worker control action")

        metadata |= {
            "action": normalized_action,
            "worker_status": worker.status,
            "runtime_status": runtime.status,
            "connection_status": runtime.connection_status,
            "affected_claims": affected_claims,
            "affected_runs": affected_runs,
        }
        self._events.append_runtime_event(
            runtime,
            f"self_hosted.worker_{normalized_action}",
            reason or worker.machine_id,
            metadata,
        )
        self._events.append_runtime_space_event(
            runtime,
            f"self_hosted.worker_{normalized_action}",
            reason or worker.machine_id,
            metadata,
        )
        self._session.commit()
        self._session.refresh(worker)
        self._session.refresh(runtime)
        return WorkerControlResult(
            worker=worker,
            runtime=runtime,
            action=normalized_action,
            affected_claims=affected_claims,
            affected_runs=affected_runs,
        )


def _connection_status_after_resume(last_heartbeat_at: datetime | None, now: datetime) -> str:
    if last_heartbeat_at is None:
        return "offline"
    heartbeat = (
        last_heartbeat_at
        if last_heartbeat_at.tzinfo
        else last_heartbeat_at.replace(tzinfo=UTC)
    )
    return "online" if now - heartbeat <= timedelta(seconds=300) else "degraded"
