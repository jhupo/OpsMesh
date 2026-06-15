from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

from sqlalchemy.orm import Session

from backend.app.core.trace_context import current_trace_metadata
from backend.app.operations.service import OperationsService
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.runner_models import WorkerRunnerConfig

logger = logging.getLogger(__name__)

SessionScope = Callable[[], Iterator[Session]]


class WorkerLeaseReporter:
    def __init__(
        self,
        *,
        config: WorkerRunnerConfig,
        session_scope: SessionScope,
    ) -> None:
        self._config = config
        self._session_scope = session_scope

    def start_lease(self, job: JobPayload) -> None:
        try:
            with self._session_scope() as session:
                OperationsService(session).start_worker_lease(
                    worker_id=self._config.worker_id,
                    queue_name=self._config.queue_name,
                    job=job,
                    metadata={
                        "priority": job.priority,
                        "requested_by_user_id": str(job.requested_by_user_id)
                        if job.requested_by_user_id is not None
                        else None,
                        "requested_by_agent_run_id": str(job.requested_by_agent_run_id)
                        if job.requested_by_agent_run_id is not None
                        else None,
                        "routing": dict(job.routing),
                        **(job.trace_metadata() or current_trace_metadata()),
                    },
                )
        except Exception:
            logger.exception("Failed to start worker lease")

    def finish_lease(
        self,
        job: JobPayload,
        *,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        try:
            lease_metadata = job.trace_metadata() or current_trace_metadata()
            lease_metadata.update(metadata or {})
            with self._session_scope() as session:
                OperationsService(session).finish_worker_lease(
                    job_id=job.job_id,
                    status=status,
                    metadata=lease_metadata,
                )
        except Exception:
            logger.exception("Failed to finish worker lease")

    def record_team_execution_loop_failure(
        self,
        job: JobPayload,
        *,
        status: str,
        error: BaseException,
    ) -> None:
        if job.job_type != JobType.TEAM_EXECUTION_LOOP:
            return
        try:
            with self._session_scope() as session:
                TeamRuntimeService(session).record_worker_failure(
                    workspace_id=job.workspace_id,
                    team_id=job.resource_id,
                    worker_id=self._config.worker_id,
                    queue_name=self._config.queue_name,
                    job_id=job.job_id,
                    status=status,
                    attempt=job.attempt,
                    max_attempts=job.max_attempts,
                    will_retry=job.can_retry,
                    error=error,
                    actor_user_id=job.requested_by_user_id,
                    routing=job.routing,
                    trace_metadata=job.trace_metadata() or current_trace_metadata(),
                )
        except Exception:
            logger.exception("Failed to record team execution loop worker failure")
