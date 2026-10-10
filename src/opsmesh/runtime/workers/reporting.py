from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from opsmesh.runtime.queues.contracts import JobPayload
from opsmesh.runtime.workers.leases import WorkerLeaseWriter
from opsmesh.runtime.workers.models import WorkerRunnerConfig
from opsmesh.shared.telemetry.trace_context import current_trace_metadata

SessionScope = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)


class WorkerLeaseReporter:
    def __init__(
        self,
        *,
        config: WorkerRunnerConfig,
        session_scope: SessionScope,
    ) -> None:
        self._config = config
        self._session_scope = session_scope

    def start_lease(
        self,
        job: JobPayload,
        *,
        claim_token: str,
        session: Session | None = None,
    ) -> bool:
        if session is not None:
            return self._start_lease(session, job, claim_token=claim_token)
        with self._session_scope() as lease_session:
            return self._start_lease(lease_session, job, claim_token=claim_token)

    def heartbeat_lease(self, job: JobPayload, *, claim_token: str) -> bool:
        with self._session_scope() as session:
            return WorkerLeaseWriter(session).heartbeat_worker_lease(
                job_id=job.job_id,
                claim_token=claim_token,
            )

    def finish_lease(
        self,
        job: JobPayload,
        *,
        claim_token: str,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        lease_metadata = job.trace_metadata() or current_trace_metadata()
        lease_metadata.update(metadata or {})
        with self._session_scope() as session:
            return (
                WorkerLeaseWriter(session).finish_worker_lease(
                    job_id=job.job_id,
                    claim_token=claim_token,
                    status=status,
                    metadata=lease_metadata,
                )
                is not None
            )

    def _start_lease(self, session: Session, job: JobPayload, *, claim_token: str) -> bool:
        lease = WorkerLeaseWriter(session).start_worker_lease(
            worker_id=self._config.worker_id,
            queue_name=self._config.queue_name,
            job=job,
            claim_token=claim_token,
            metadata={
                "priority": job.priority,
                "idempotency_key": job.idempotency_key,
                "max_attempts": job.max_attempts,
                "requested_by_user_id": str(job.requested_by_user_id)
                if job.requested_by_user_id is not None
                else None,
                "requested_by_agent_run_id": str(job.requested_by_agent_run_id)
                if job.requested_by_agent_run_id is not None
                else None,
                "request_id": job.request_id,
                "routing": dict(job.routing),
                **(job.trace_metadata() or current_trace_metadata()),
            },
        )
        return lease.status == "running" and lease.claim_token == claim_token
