from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.operations.models import WorkerLease, WorkerNode


class WorkerLifecycleQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def worker_nodes_by_worker_id(self) -> dict[str, WorkerNode]:
        return {
            node.worker_id: node for node in self._session.scalars(select(WorkerNode)).all()
        }

    def worker_leases(self, workspace_id: UUID) -> list[WorkerLease]:
        return list(
            self._session.scalars(
                select(WorkerLease).where(WorkerLease.workspace_id == workspace_id)
            ).all()
        )
