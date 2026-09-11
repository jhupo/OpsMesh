from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.runtime_manager.models import RuntimeLease

T = TypeVar("T")


class RuntimeLeaseOperationsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_runtime_leases(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        runtime_space_id: UUID | None = None,
    ) -> tuple[list[RuntimeLease], int]:
        statement = select(RuntimeLease).where(RuntimeLease.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(RuntimeLease.status == status)
        if runtime_space_id is not None:
            statement = statement.where(RuntimeLease.runtime_space_id == runtime_space_id)
        return self._page(statement.order_by(RuntimeLease.created_at.desc()), page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
