from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.operations.models import WorkerLease
from backend.app.operations.worker_lifecycle import RUNNING_LEASE_STATUSES

T = TypeVar("T")


class WorkerLeaseQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_worker_leases(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        worker_id: str | None = None,
    ) -> tuple[list[WorkerLease], int]:
        statement = select(WorkerLease).where(WorkerLease.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(WorkerLease.status == status)
        if worker_id is not None:
            statement = statement.where(WorkerLease.worker_id == worker_id)
        return self._page(statement.order_by(WorkerLease.created_at.desc()), page)

    def running_leases_for_worker(self, worker_id: str) -> int:
        running = self._session.scalar(
            select(func.count())
            .select_from(WorkerLease)
            .where(
                WorkerLease.worker_id == worker_id,
                WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
            )
        )
        return int(running or 0)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
