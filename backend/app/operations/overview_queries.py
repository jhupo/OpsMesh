from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.operations.models import WorkerHeartbeat
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.security.models import SecurityEvent


class OperationsOverviewQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def failed_runs_count(self, workspace_id: UUID) -> int:
        return self._count(
            select(func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.status == "failed",
            )
        )

    def offline_runtimes_count(self, workspace_id: UUID) -> int:
        return self._count(
            select(func.count())
            .select_from(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.connection_status == "offline",
                WorkspaceRuntime.execution_run_id.is_(None),
            )
        )

    def workers_online_count(self, workspace_id: UUID) -> int:
        return self._count(
            select(func.count())
            .select_from(WorkerHeartbeat)
            .where(
                WorkerHeartbeat.workspace_id == workspace_id,
                WorkerHeartbeat.status == "online",
            )
        )

    def security_warnings_count(self, workspace_id: UUID) -> int:
        return self._count(
            select(func.count())
            .select_from(SecurityEvent)
            .where(
                SecurityEvent.workspace_id == workspace_id,
                SecurityEvent.severity.in_(["warning", "critical"]),
            )
        )

    def _count(self, statement: Select[tuple[int]]) -> int:
        return int(self._session.scalar(statement) or 0)
