from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.projects.models import AgentRunProjectIOState
from backend.app.projects.runtime_io import RunProjectIOService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import DockerRuntimeClient
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.runtime_manager.run_environment import RunRuntimeEnvironmentService
from backend.app.runtime_manager.spaces.models import RuntimeSpaceEvent


class RuntimeCleanupService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def cleanup_stale_runtimes(
        self,
        workspace_id: UUID,
        *,
        stale_after_seconds: int = 600,
    ) -> tuple[int, int]:
        stale_runtimes = self._stale_runtimes(
            stale_after_seconds=stale_after_seconds,
            workspace_id=workspace_id,
        )
        self._mark_runtimes_offline(
            stale_runtimes,
            source="operations.cleanup",
        )
        deleted_records = self._mark_deleted_terminal_runtimes(workspace_id)
        self._session.commit()
        return len(stale_runtimes), deleted_records

    def cleanup_stale_runtimes_across_workspaces(
        self,
        *,
        stale_after_seconds: int = 600,
    ) -> tuple[int, int]:
        stale_runtimes = self._stale_runtimes(stale_after_seconds=stale_after_seconds)
        self._mark_runtimes_offline(
            stale_runtimes,
            source="worker.maintenance",
        )
        deleted_records = self._mark_deleted_terminal_runtimes(source="worker.maintenance")
        self._session.commit()
        return len(stale_runtimes), deleted_records

    def cleanup_terminal_run_workspaces(
        self,
        *,
        settings: Settings | None,
        docker_client: DockerRuntimeClient | None,
        workspace_id: UUID | None = None,
        limit: int = 100,
    ) -> tuple[int, int]:
        """Reclaim run-scoped project workspaces after every terminal outcome.

        The cleanup state is durable, so a worker interruption leaves the row eligible for the
        next maintenance pass. A missing Docker client is treated as a retryable infrastructure
        condition rather than silently claiming that files were removed.
        """
        statement = (
            select(AgentRunProjectIOState)
            .join(
                AgentRun,
                (AgentRun.workspace_id == AgentRunProjectIOState.workspace_id)
                & (AgentRun.id == AgentRunProjectIOState.agent_run_id),
            )
            .where(
                AgentRunProjectIOState.cleanup_status.in_(("pending", "running", "failed")),
                AgentRun.status.in_(
                    (
                        RunStatus.COMPLETED.value,
                        RunStatus.FAILED.value,
                        RunStatus.CANCELLED.value,
                    )
                ),
            )
            .order_by(AgentRunProjectIOState.updated_at.asc())
            .limit(limit)
        )
        if workspace_id is not None:
            statement = statement.where(AgentRunProjectIOState.workspace_id == workspace_id)
        states = list(self._session.scalars(statement).all())
        completed = 0
        failed = 0
        for state in states:
            run = self._session.scalar(
                select(AgentRun).where(
                    AgentRun.workspace_id == state.workspace_id,
                    AgentRun.id == state.agent_run_id,
                )
            )
            if run is None:
                continue
            if docker_client is None or settings is None:
                failed += 1
                continue
            if RunProjectIOService(
                self._session,
                storage=None,
                docker_client=docker_client,
                settings=settings,
            ).cleanup_runtime_workspace(run, reason="worker_maintenance"):
                completed += 1
            else:
                failed += 1
        self._session.commit()
        return completed, failed

    def cleanup_terminal_run_environments(
        self,
        *,
        docker_client: DockerRuntimeClient | None,
        workspace_id: UUID | None = None,
        limit: int = 100,
    ) -> tuple[int, int]:
        """Reclaim ephemeral containers and volumes left by terminal or orphaned runs."""
        statement = (
            select(AgentRun)
            .join(
                WorkspaceRuntime,
                (WorkspaceRuntime.workspace_id == AgentRun.workspace_id)
                & (WorkspaceRuntime.id == AgentRun.execution_runtime_id),
            )
            .where(
                AgentRun.execution_runtime_id.is_not(None),
                WorkspaceRuntime.status != "deleted",
                AgentRun.status.in_(
                    (
                        RunStatus.COMPLETED.value,
                        RunStatus.FAILED.value,
                        RunStatus.CANCELLED.value,
                    )
                ),
            )
            .order_by(AgentRun.updated_at.asc())
            .limit(limit)
        )
        if workspace_id is not None:
            statement = statement.where(AgentRun.workspace_id == workspace_id)
        runs = list(self._session.scalars(statement).all())
        completed = 0
        failed = 0
        service = RunRuntimeEnvironmentService(self._session, docker_client)
        for run in runs:
            if service.cleanup_for_run(run):
                completed += 1
            else:
                failed += 1
        self._session.commit()
        return completed, failed

    def cleanup_orphaned_pool_leases(
        self,
        *,
        docker_client: DockerRuntimeClient | None,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 600,
        limit: int = 100,
    ) -> tuple[int, int]:
        """Reclaim pool members left leased by a terminal or lost worker run."""
        if docker_client is None:
            return 0, limit
        result = RunRuntimeEnvironmentService(
            self._session,
            docker_client,
        ).reclaim_orphaned_pool_leases(
            workspace_id=workspace_id,
            stale_after_seconds=stale_after_seconds,
            limit=limit,
        )
        self._session.commit()
        return result

    def _stale_runtimes(
        self,
        *,
        stale_after_seconds: int,
        workspace_id: UUID | None = None,
    ) -> list[WorkspaceRuntime]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        statement = select(WorkspaceRuntime).where(
            WorkspaceRuntime.connection_status == "online",
            WorkspaceRuntime.last_heartbeat_at.is_not(None),
            WorkspaceRuntime.last_heartbeat_at < cutoff,
            WorkspaceRuntime.execution_run_id.is_(None),
        )
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        return list(self._session.scalars(statement).all())

    def _mark_runtimes_offline(
        self,
        runtimes: list[WorkspaceRuntime],
        *,
        source: str,
    ) -> None:
        now = datetime.now(UTC)
        for runtime in runtimes:
            runtime.connection_status = "offline"
            self._append_runtime_space_event(
                runtime,
                "runtime.marked_offline",
                "Runtime heartbeat is stale",
                {"source": source},
                created_at=now,
            )

    def _mark_deleted_terminal_runtimes(
        self,
        workspace_id: UUID | None = None,
        *,
        source: str = "operations.cleanup",
    ) -> int:
        statement = select(WorkspaceRuntime).where(
            WorkspaceRuntime.status.in_(["stopped", "failed"])
        )
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        terminal = self._session.scalars(statement).all()
        now = datetime.now(UTC)
        for runtime in terminal:
            runtime.status = "deleted"
            runtime.connection_status = "offline"
            self._append_runtime_space_event(
                runtime,
                "runtime.record_deleted",
                "Terminal runtime record marked deleted",
                {"source": source},
                created_at=now,
            )
        return len(terminal)

    def _append_runtime_space_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object],
        *,
        created_at: datetime,
    ) -> None:
        if runtime.runtime_space_id is None:
            return
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime.workspace_id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                event_metadata={"runtime_id": str(runtime.id), **metadata},
                created_at=created_at,
            )
        )
