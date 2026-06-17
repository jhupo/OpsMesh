from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime_manager.metadata import runtime_space_reservation_key
from backend.app.runtime_spaces.reservation_release import RuntimeSpaceReservationReleaseService
from backend.app.runtimes.models import RuntimeLease, WorkspaceRuntime


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
