from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.runtime.environment.contracts import DockerRuntimeClient
from backend.app.runtime.environment.leases import RuntimeLeaseStore
from backend.app.runtime.environment.manager import RuntimeManager
from backend.app.runtime.environment.models import RuntimeLease, WorkspaceRuntime
from backend.app.runtime.environment.pool.leases import pool_lease_run_id

CleanupRun = Callable[[AgentRun], bool]
RecordFailedRun = Callable[[AgentRun, UUID, UUID], None]


class RuntimePoolReclaimer:
    """Recover terminal or abandoned pool ownership from durable lease evidence."""

    def __init__(
        self,
        session: Session,
        docker_client: DockerRuntimeClient | None,
        *,
        cleanup_run: CleanupRun,
        record_failed_run: RecordFailedRun,
    ) -> None:
        self._session = session
        self._docker = docker_client
        self._cleanup_run = cleanup_run
        self._record_failed_run = record_failed_run

    def reclaim(
        self,
        *,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 600,
        limit: int = 100,
    ) -> tuple[int, int]:
        cutoff = datetime.now(UTC).timestamp() - stale_after_seconds
        statement = (
            select(RuntimeLease)
            .join(
                WorkspaceRuntime,
                (WorkspaceRuntime.workspace_id == RuntimeLease.workspace_id)
                & (WorkspaceRuntime.id == RuntimeLease.workspace_runtime_id),
            )
            .where(
                RuntimeLease.status == "leased",
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.execution_pool_member_id.is_(None),
            )
            .order_by(RuntimeLease.updated_at.asc())
            .limit(limit)
        )
        if workspace_id is not None:
            statement = statement.where(RuntimeLease.workspace_id == workspace_id)
        reclaimed = failed = 0
        for lease in self._session.scalars(statement).all():
            member = self._session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == lease.workspace_id,
                    WorkspaceRuntime.id == lease.workspace_runtime_id,
                )
            )
            if member is None:
                continue
            run_id = pool_lease_run_id(lease)
            run = (
                self._session.scalar(
                    select(AgentRun).where(
                        AgentRun.workspace_id == lease.workspace_id,
                        AgentRun.id == run_id,
                    )
                )
                if run_id is not None
                else None
            )
            if run is not None and run.status in {"completed", "failed", "cancelled"}:
                if self._cleanup_run(run):
                    reclaimed += 1
                else:
                    failed += 1
                continue
            lease_age = lease.updated_at.timestamp() if lease.updated_at is not None else 0
            if run is not None and lease_age >= cutoff:
                continue
            child = self._pooled_child(lease.workspace_id, run_id, member.id)
            if self._destroy_orphan(member, child, run):
                reclaimed += 1
            else:
                failed += 1
        self._session.flush()
        return reclaimed, failed

    def _pooled_child(
        self,
        workspace_id: UUID,
        run_id: UUID | None,
        member_id: UUID,
    ) -> WorkspaceRuntime | None:
        if run_id is None:
            return None
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.execution_run_id == run_id,
                WorkspaceRuntime.execution_pool_member_id == member_id,
                WorkspaceRuntime.status != "deleted",
            )
        )

    def _destroy_orphan(
        self,
        member: WorkspaceRuntime,
        child: WorkspaceRuntime | None,
        run: AgentRun | None,
    ) -> bool:
        if self._docker is None:
            return False
        try:
            RuntimeManager(self._session, self._docker).delete_runtime(
                member,
                allow_active_pool_lease=True,
            )
        except Exception:
            member.status = "cleanup_failed"
            member.connection_status = "offline"
            return False
        if child is not None:
            child.status = "deleted"
            child.connection_status = "offline"
            RuntimeLeaseStore(self._session).set_status(
                child,
                "released",
                released_at=datetime.now(UTC),
            )
        if run is not None:
            self._record_failed_run(run, child.id if child is not None else member.id, member.id)
        return True
