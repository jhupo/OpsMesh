from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.teams.execution_loop_jobs import enqueue_team_execution_loop_job
from backend.app.teams.execution_loop_runtime_candidates import (
    _increment_skip_reason,
    _record_runtime_scheduler_scan,
    _scheduler_scan_candidate,
)
from backend.app.teams.models import AgentTeam
from backend.app.workers.queue.redis_queue import RedisQueue


class TeamExecutionLoopQueueDispatcher:
    """Enqueue unique team execution loop candidates and record scheduler scans."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_candidates(
        self,
        *,
        queue: RedisQueue,
        candidates: list[dict[str, object]],
        limit: int,
        window: int,
        generated_at: datetime,
    ) -> tuple[int, int, dict[str, int]]:
        seen: set[tuple[UUID, UUID]] = set()
        enqueued = 0
        skipped = 0
        skipped_reasons: dict[str, int] = {}
        for candidate in candidates:
            team_key = (candidate["workspace_id"], candidate["team_id"])
            if team_key in seen:
                continue
            seen.add(team_key)
            accepted = self._enqueue_candidate(
                queue=queue,
                candidate=candidate,
                window=window,
            )
            if accepted:
                enqueued += 1
                self._record_enqueued_runtime_scan(candidate, generated_at, window)
            else:
                skipped += 1
                _increment_skip_reason(skipped_reasons, "queue_idempotency_duplicate")
                self._record_duplicate_runtime_scan(candidate, generated_at, window)
            if enqueued >= limit:
                break
        return enqueued, skipped, skipped_reasons

    def _enqueue_candidate(
        self,
        *,
        queue: RedisQueue,
        candidate: dict[str, object],
        window: int,
    ) -> bool:
        return enqueue_team_execution_loop_job(
            queue=queue,
            workspace_id=candidate["workspace_id"],
            team_id=candidate["team_id"],
            requested_by_user_id=candidate["requested_by_user_id"],
            idempotency_suffix=f"maintenance:{window}",
            priority=candidate["priority"],
            routing={
                "source": "worker_maintenance",
                "trigger": candidate["trigger"],
                **dict(candidate.get("routing") or {}),
                "task_id": (
                    str(candidate["task_id"]) if candidate["task_id"] is not None else None
                ),
            },
        )

    def _record_enqueued_runtime_scan(
        self,
        candidate: dict[str, object],
        generated_at: datetime,
        window: int,
    ) -> None:
        if candidate["task_id"] is not None:
            return
        team = self._session.get(AgentTeam, candidate["team_id"])
        if team is not None:
            _record_runtime_scheduler_scan(
                team,
                status="enqueued",
                reason=None,
                scanned_at=generated_at,
                window=window,
                runtime_candidate=_scheduler_scan_candidate(candidate),
            )

    def _record_duplicate_runtime_scan(
        self,
        candidate: dict[str, object],
        generated_at: datetime,
        window: int,
    ) -> None:
        if candidate["task_id"] is not None:
            return
        team = self._session.get(AgentTeam, candidate["team_id"])
        if team is not None:
            _record_runtime_scheduler_scan(
                team,
                status="skipped",
                reason="queue_idempotency_duplicate",
                scanned_at=generated_at,
                window=window,
                runtime_candidate=_scheduler_scan_candidate(candidate),
            )
