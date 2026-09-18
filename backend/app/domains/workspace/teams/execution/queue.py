from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.access.resources import ResourceAccessDenied
from backend.app.domains.workspace.teams.execution.loop_support import (
    TEAM_EXECUTION_LOOP_WINDOW_SECONDS,
    enqueue_team_execution_loop_job,
)
from backend.app.domains.workspace.teams.execution.queue_repository import (
    TeamExecutionLoopQueueRepository,
)
from backend.app.domains.workspace.teams.execution.runtime_candidates import (
    TeamExecutionLoopRuntimeCandidateBuilder,
    TeamLoopCandidate,
    _increment_skip_reason,
    _record_runtime_scheduler_scan,
    _scheduler_scan_candidate,
    _team_loop_candidate,
)
from backend.app.domains.workspace.teams.models import AgentTeam
from backend.app.runtime.workers.queue import RedisQueue


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
        candidates: list[TeamLoopCandidate] = [
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
            if runtime_candidate["skip"]:
                if skip_reason is None:
                    raise ValueError("Blocked runtime candidate must include a skip reason")
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


class TeamExecutionLoopQueueDispatcher:
    """Enqueue unique team execution loop candidates and record scheduler scans."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_candidates(
        self,
        *,
        queue: RedisQueue,
        candidates: list[TeamLoopCandidate],
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
            try:
                accepted = self._enqueue_candidate(
                    queue=queue,
                    candidate=candidate,
                    window=window,
                )
            except ResourceAccessDenied:
                skipped += 1
                _increment_skip_reason(skipped_reasons, "authorization_revoked")
                continue
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
        candidate: TeamLoopCandidate,
        window: int,
    ) -> bool:
        return enqueue_team_execution_loop_job(
            session=self._session,
            queue=queue,
            workspace_id=candidate["workspace_id"],
            team_id=candidate["team_id"],
            requested_by_user_id=candidate["requested_by_user_id"],
            idempotency_suffix=f"maintenance:{window}",
            priority=candidate["priority"],
            routing={
                "source": "worker_maintenance",
                "trigger": candidate["trigger"],
                **candidate["routing"],
                "task_id": (
                    str(candidate["task_id"]) if candidate["task_id"] is not None else None
                ),
            },
        )

    def _record_enqueued_runtime_scan(
        self,
        candidate: TeamLoopCandidate,
        generated_at: datetime,
        window: int,
    ) -> None:
        if candidate["task_id"] is not None:
            return
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == candidate["workspace_id"],
                AgentTeam.id == candidate["team_id"],
            )
        )
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
        candidate: TeamLoopCandidate,
        generated_at: datetime,
        window: int,
    ) -> None:
        if candidate["task_id"] is not None:
            return
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == candidate["workspace_id"],
                AgentTeam.id == candidate["team_id"],
            )
        )
        if team is not None:
            _record_runtime_scheduler_scan(
                team,
                status="skipped",
                reason="queue_idempotency_duplicate",
                scanned_at=generated_at,
                window=window,
                runtime_candidate=_scheduler_scan_candidate(candidate),
            )
