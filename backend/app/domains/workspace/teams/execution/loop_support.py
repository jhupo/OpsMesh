"""Shared execution-loop infrastructure: constants, queueing, persistence and lookup."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.teams.models import AgentTeam as _AgentTeamModel
from backend.app.domains.workspace.teams.runtime.service import TeamRuntimeService
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.queue import RedisQueue

COMPLETED_STEP_STATUSES = {"completed", "cancelled", "skipped"}
TEAM_EXECUTION_LOOP_WINDOW_SECONDS = 60
TEAM_RUNTIME_DEFAULT_LOOP_INTERVAL_SECONDS = 300


def enqueue_team_execution_loop_job(
    *,
    queue: RedisQueue,
    workspace_id: UUID,
    team_id: UUID,
    requested_by_user_id: UUID | None,
    idempotency_suffix: str,
    priority: int = 0,
    routing: dict[str, object] | None = None,
) -> bool:
    return queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.TEAM_EXECUTION_LOOP,
            resource_id=team_id,
            requested_by_user_id=requested_by_user_id,
            idempotency_key=f"team.execution_loop:{workspace_id}:{team_id}:{idempotency_suffix}",
            priority=priority,
            routing=routing or {},
        )
    )


class TeamExecutionLoopIterationRecorder:
    """Persist execution loop heartbeats and audit events."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        TeamRuntimeService(self._session).record_iteration(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=status,
            summary=summary,
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="team.execution_loop.iteration_ran",
            target_type="agent_team",
            target_id=team_id,
            metadata=summary,
        )
        self._session.commit()


class TeamExecutionLoopRepository:
    """Read team execution loop prerequisites."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def team(self, *, workspace_id: UUID, team_id: UUID) -> _AgentTeamModel | None:
        return self._session.scalar(
            select(_AgentTeamModel).where(
                _AgentTeamModel.workspace_id == workspace_id, _AgentTeamModel.id == team_id
            )
        )

    def team_exists(self, *, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(_AgentTeamModel.id).where(
                    _AgentTeamModel.workspace_id == workspace_id, _AgentTeamModel.id == team_id
                )
            )
            is not None
        )
