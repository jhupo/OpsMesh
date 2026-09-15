from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime.environment.models import RuntimeLease, WorkspaceRuntime


class RuntimePoolLeaseStore:
    """Atomic ownership boundary for a reusable runtime pool member."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def acquire(
        self,
        runtime: WorkspaceRuntime,
        *,
        run_id: UUID,
        metadata: dict[str, object],
    ) -> RuntimeLease | None:
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
            if _pool_run_id(lease.lease_metadata) != str(run_id):
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

    def release(
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
        released_at = datetime.now(UTC)
        metadata["pool"] = {
            **pool_metadata,
            "state": "available",
            "run_id": None,
            "released_at": released_at.isoformat(),
        }
        lease.status = "active"
        lease.lease_metadata = metadata
        lease.released_at = released_at
        self._session.flush([lease])
        return lease


def pool_lease_run_id(lease: RuntimeLease) -> UUID | None:
    value = _pool_run_id(lease.lease_metadata)
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _pool_run_id(metadata: dict[str, object] | None) -> str | None:
    pool = metadata.get("pool") if isinstance(metadata, dict) else None
    value = pool.get("run_id") if isinstance(pool, dict) else None
    return value if isinstance(value, str) else None
