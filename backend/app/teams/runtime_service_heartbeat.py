from __future__ import annotations

from uuid import UUID


class TeamRuntimeHeartbeatMixin:
    def record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        self._heartbeat.record_iteration(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )

    def record_worker_failure(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        worker_id: str,
        queue_name: str,
        job_id: UUID,
        status: str,
        attempt: int,
        max_attempts: int,
        will_retry: bool,
        error: BaseException,
        actor_user_id: UUID | None = None,
        routing: dict[str, object] | None = None,
        trace_metadata: dict[str, object] | None = None,
    ) -> None:
        self._heartbeat.record_worker_failure(
            workspace_id=workspace_id,
            team_id=team_id,
            worker_id=worker_id,
            queue_name=queue_name,
            job_id=job_id,
            status=status,
            attempt=attempt,
            max_attempts=max_attempts,
            will_retry=will_retry,
            error=error,
            actor_user_id=actor_user_id,
            routing=routing,
            trace_metadata=trace_metadata,
        )
