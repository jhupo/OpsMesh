from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from backend.app.runtime.queues.contracts import JobPayload, JobType
from backend.app.runtime.workers.models import WorkerRunnerConfig
from backend.app.shared.telemetry.trace_context import current_trace_metadata
from backend.app.teams.sessions.service import TeamRuntimeService

SessionScope = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)


class TeamWorkerFailureReporter:
    def __init__(self, *, config: WorkerRunnerConfig, session_scope: SessionScope) -> None:
        self._config = config
        self._session_scope = session_scope

    def record_failure(
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
