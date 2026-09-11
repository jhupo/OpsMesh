from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.metadata import runtime_space_reservation_key
from backend.app.runtime_spaces.reservation_release import RuntimeSpaceReservationReleaseService
from backend.app.runtime_manager.models import RuntimeLease, WorkspaceRuntime


class RuntimeLeaseStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure(
        self,
        runtime: WorkspaceRuntime,
        *,
        status: str,
        metadata: dict[str, object],
    ) -> RuntimeLease:
        lease = self._session.scalar(
            select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
        )
        now = datetime.now(UTC)
        if lease is None:
            lease = RuntimeLease(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                docker_container_id=runtime.docker_container_id,
                status=status,
                lease_metadata=metadata,
                acquired_at=now,
            )
            self._session.add(lease)
            self._session.flush([lease])
            return lease
        lease.runtime_space_id = runtime.runtime_space_id
        lease.docker_container_id = runtime.docker_container_id
        lease.status = status
        lease.lease_metadata = lease.lease_metadata | metadata
        if status not in {"released", "cleanup_failed"}:
            lease.released_at = None
        self._session.flush([lease])
        return lease

    def set_status(
        self,
        runtime: WorkspaceRuntime,
        status: str,
        *,
        released_at: datetime | None = None,
    ) -> RuntimeLease | None:
        lease = self._session.scalar(
            select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
        )
        if lease is None:
            return None
        lease.status = status
        lease.runtime_space_id = runtime.runtime_space_id
        lease.docker_container_id = runtime.docker_container_id
        if released_at is not None:
            lease.released_at = released_at
        self._session.flush([lease])
        return lease

    def acquire_pool_member(
        self,
        runtime: WorkspaceRuntime,
        *,
        run_id: UUID,
        metadata: dict[str, object],
    ) -> RuntimeLease | None:
        """Atomically reserve one pre-provisioned pooled runtime for a run.

        The database lease is the concurrency boundary. A pooled container is never selected
        from a stale ORM snapshot without first locking its lease row.
        """
        lease = self._session.scalar(
            select(RuntimeLease)
            .where(RuntimeLease.workspace_runtime_id == runtime.id)
            .with_for_update(skip_locked=True)
        )
        if lease is None:
            lease = RuntimeLease(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                docker_container_id=runtime.docker_container_id,
                status="active",
                lease_metadata={},
                acquired_at=datetime.now(UTC),
            )
            self._session.add(lease)
            self._session.flush([lease])
        if lease.status == "leased":
            current_run_id = _pool_run_id(lease.lease_metadata)
            if current_run_id != str(run_id):
                return None
        elif lease.status not in {"active", "running", "available", "released"}:
            return None
        lease.status = "leased"
        lease.runtime_space_id = runtime.runtime_space_id
        lease.docker_container_id = runtime.docker_container_id
        lease.released_at = None
        lease.lease_metadata = {
            **dict(lease.lease_metadata or {}),
            "pool": {
                "state": "leased",
                "run_id": str(run_id),
                **metadata,
            },
        }
        self._session.flush([lease])
        return lease

    def release_pool_member(
        self,
        runtime: WorkspaceRuntime,
        *,
        run_id: UUID,
    ) -> RuntimeLease | None:
        lease = self._session.scalar(
            select(RuntimeLease)
            .where(RuntimeLease.workspace_runtime_id == runtime.id)
            .with_for_update(skip_locked=True)
        )
        if lease is None:
            return None
        current_run_id = _pool_run_id(lease.lease_metadata)
        if lease.status == "leased" and current_run_id not in {None, str(run_id)}:
            raise ValueError("Pooled runtime lease is owned by another run")
        metadata = dict(lease.lease_metadata or {})
        pool_value = metadata.get("pool")
        pool_metadata = dict(pool_value) if isinstance(pool_value, dict) else {}
        metadata["pool"] = {
            **pool_metadata,
            "state": "available",
            "run_id": None,
            "released_at": datetime.now(UTC).isoformat(),
        }
        lease.status = "active"
        lease.lease_metadata = metadata
        lease.released_at = datetime.now(UTC)
        self._session.flush([lease])
        return lease


class RuntimeSpaceReservationStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def release(self, runtime: WorkspaceRuntime) -> bool:
        if runtime.runtime_space_id is None:
            return False
        return RuntimeSpaceReservationReleaseService(self._session).release_reservation_by_key(
            workspace_id=runtime.workspace_id,
            runtime_space_id=runtime.runtime_space_id,
            reservation_key=runtime_space_reservation_key(runtime),
            released_at=datetime.now(UTC),
        )


def _pool_run_id(metadata: dict[str, object] | None) -> str | None:
    pool = metadata.get("pool") if isinstance(metadata, dict) else None
    if not isinstance(pool, dict):
        return None
    value = pool.get("run_id")
    return value if isinstance(value, str) else None
