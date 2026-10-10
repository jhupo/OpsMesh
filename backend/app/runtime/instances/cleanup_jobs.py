from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.runs.state import RunStatus
from backend.app.runtime.backends.registry import RuntimeBackendRegistry
from backend.app.runtime.instances.contracts import DockerRuntimeClient
from backend.app.runtime.instances.models import RuntimeAllocation, WorkspaceRuntime
from backend.app.runtime.instances.run_environment import RunRuntimeEnvironmentService
from backend.app.runtime.spaces.models import RuntimeSpaceEvent
from backend.app.shared.config import Settings
from backend.app.workspaces.projects.io.service import RunProjectIOService
from backend.app.workspaces.projects.models import AgentRunProjectIOState


class RuntimeCleanupService:
    def __init__(self, session: Session, docker_client: DockerRuntimeClient | None = None) -> None:
        self._session = session
        self._docker_client = docker_client

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
        runtime_backends: RuntimeBackendRegistry,
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
                runtime_backends=runtime_backends,
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
                or_(
                    (WorkspaceRuntime.execution_mode == "isolated")
                    & (WorkspaceRuntime.status != "deleted"),
                    exists(
                        select(RuntimeAllocation.id).where(
                            RuntimeAllocation.workspace_id == AgentRun.workspace_id,
                            RuntimeAllocation.owner_kind == "run",
                            RuntimeAllocation.owner_id == AgentRun.id,
                        )
                    ),
                    AgentRun.input["runtime_execution"]["status"].as_string() == "suspended",
                ),
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

    def _stale_runtimes(
        self,
        *,
        stale_after_seconds: int,
        workspace_id: UUID | None = None,
    ) -> list[WorkspaceRuntime]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        statement = select(WorkspaceRuntime).where(
            or_(
                WorkspaceRuntime.connection_status == "online",
                (WorkspaceRuntime.runtime_provider == "cloud_docker")
                & (WorkspaceRuntime.status == "running")
                & (WorkspaceRuntime.connection_status == "offline"),
            ),
            WorkspaceRuntime.last_heartbeat_at.is_not(None),
            WorkspaceRuntime.last_heartbeat_at < cutoff,
            WorkspaceRuntime.execution_run_id.is_(None),
        )
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        stale: list[WorkspaceRuntime] = []
        for runtime in self._session.scalars(statement).all():
            if runtime.runtime_provider == "cloud_docker" and runtime.docker_container_id:
                if self._docker_client is None:
                    continue
                # Managed containers do not emit worker heartbeat leases. Inspect them instead.
                if self._docker_client.container_running(runtime.docker_container_id):
                    if runtime.connection_status == "offline":
                        self._append_runtime_space_event(
                            runtime,
                            "runtime.connection_restored",
                            "Managed container is running",
                            {"source": "container_probe"},
                            created_at=datetime.now(UTC),
                        )
                    runtime.connection_status = "online"
                    runtime.last_heartbeat_at = datetime.now(UTC)
                    continue
            if runtime.connection_status == "offline":
                continue
            stale.append(runtime)
        return stale

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
            WorkspaceRuntime.status.in_(["stopped", "failed"]),
            WorkspaceRuntime.docker_container_id.is_(None),
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
