from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.operations.models import WorkerNode

T = TypeVar("T")


class WorkerNodeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def by_worker_id(self, worker_id: str) -> WorkerNode | None:
        return self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))

    def list_worker_nodes(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> tuple[list[WorkerNode], int]:
        statement = select(WorkerNode)
        if status is not None:
            statement = statement.where(WorkerNode.status == status)
        if worker_type is not None:
            statement = statement.where(WorkerNode.worker_type == worker_type)
        return page_scalars(self._session, statement.order_by(WorkerNode.last_seen_at.desc()), page)

    def add(self, node: WorkerNode) -> None:
        self._session.add(node)


def page_worker_nodes(
    session: Session,
    statement: Select[tuple[T]],
    page: PageParams,
) -> tuple[list[T], int]:
    return page_scalars(session, statement, page)
