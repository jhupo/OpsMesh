from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.teams.execution_loop_constants import TEAM_EXECUTION_LOOP_WINDOW_SECONDS
from backend.app.teams.execution_loop_queue_dispatch import (
    TeamExecutionLoopQueueDispatcher,
)
from backend.app.teams.execution_loop_queue_repository import (
    TeamExecutionLoopQueueRepository,
)
from backend.app.teams.execution_loop_queue_runtime import (
    TeamExecutionLoopRuntimeCandidateBuilder,
)
from backend.app.teams.execution_loop_runtime_candidates import (
    _increment_skip_reason,
    _record_runtime_scheduler_scan,
    _runtime_candidate_is_skip,
    _team_loop_candidate,
)
from backend.app.workers.queue.redis_queue import RedisQueue


@dataclass(frozen=True)
class TeamExecutionLoopEnqueueSummary:
    enqueued: int
    skipped: int
    skipped_reasons: dict[str, int] = field(default_factory=dict)


class TeamExecutionLoopQueueService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = TeamExecutionLoopQueueRepository(session)
        self._runtime_candidates = TeamExecutionLoopRuntimeCandidateBuilder(
            session=session,
            repo=self._repo,
        )
        self._dispatcher = TeamExecutionLoopQueueDispatcher(session)

    def enqueue_active_team_iterations(
        self,
        *,
        queue: RedisQueue,
        limit: int = 50,
        now: datetime | None = None,
    ) -> TeamExecutionLoopEnqueueSummary:
        window = int((now or datetime.now(UTC)).timestamp() // TEAM_EXECUTION_LOOP_WINDOW_SECONDS)
        generated_at = now or datetime.now(UTC)
        candidates: list[dict[str, object]] = [
            _team_loop_candidate(
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
                requested_by_user_id=task.created_by_user_id,
                priority=task.priority,
                trigger="active_team_task",
                task_id=task.id,
            )
            for task in self._repo.active_team_tasks(limit=limit)
            if task.agent_team_id is not None and task.created_by_user_id is not None
        ]
        skipped_reasons: dict[str, int] = {}
        skipped = 0
        for team in self._repo.runtime_teams(limit=limit):
            runtime_candidate, skip_reason = self._runtime_candidates.runtime_candidate(
                team,
                generated_at=generated_at,
            )
            if runtime_candidate is None:
                if skip_reason is not None:
                    skipped += 1
                    _increment_skip_reason(skipped_reasons, skip_reason)
                    _record_runtime_scheduler_scan(
                        team,
                        status="skipped",
                        reason=skip_reason,
                        scanned_at=generated_at,
                        window=window,
                    )
                continue
            if skip_reason is not None and _runtime_candidate_is_skip(runtime_candidate):
                skipped += 1
                _increment_skip_reason(skipped_reasons, skip_reason)
                _record_runtime_scheduler_scan(
                    team,
                    status="skipped",
                    reason=skip_reason,
                    scanned_at=generated_at,
                    window=window,
                    runtime_candidate=runtime_candidate,
                )
                continue
            owner_user_id = self._repo.workspace_owner_id(team.workspace_id)
            if owner_user_id is None:
                skipped += 1
                _increment_skip_reason(skipped_reasons, "workspace_owner_missing")
                _record_runtime_scheduler_scan(
                    team,
                    status="skipped",
                    reason="workspace_owner_missing",
                    scanned_at=generated_at,
                    window=window,
                    runtime_candidate=runtime_candidate,
                )
                continue
            candidates.append(
                _team_loop_candidate(
                    workspace_id=team.workspace_id,
                    team_id=team.id,
                    requested_by_user_id=owner_user_id,
                    priority=runtime_candidate["priority"],
                    trigger=runtime_candidate["trigger"],
                    task_id=None,
                    routing={
                        "runtime_health": runtime_candidate["runtime_health"],
                        "workspace_runtime_id": runtime_candidate["workspace_runtime_id"],
                        "last_heartbeat_at": runtime_candidate["last_heartbeat_at"],
                    },
                )
            )
        enqueued, dispatch_skipped, dispatch_reasons = self._dispatcher.enqueue_candidates(
            queue=queue,
            candidates=candidates,
            limit=limit,
            window=window,
            generated_at=generated_at,
        )
        skipped += dispatch_skipped
        for reason, count in dispatch_reasons.items():
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + count
        return TeamExecutionLoopEnqueueSummary(
            enqueued=enqueued,
            skipped=skipped,
            skipped_reasons=skipped_reasons,
        )
