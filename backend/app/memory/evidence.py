from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.memory.models import (
    WorkspaceMemoryEmbeddingEvent,
    WorkspaceMemoryLifecycleEvent,
    WorkspaceMemoryRetrievalEvent,
)

EvidenceT = TypeVar(
    "EvidenceT",
    WorkspaceMemoryRetrievalEvent,
    WorkspaceMemoryLifecycleEvent,
    WorkspaceMemoryEmbeddingEvent,
)


class WorkspaceMemoryEvidenceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def retrieval_events(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkspaceMemoryRetrievalEvent], int]:
        return self._page(
            select(WorkspaceMemoryRetrievalEvent).where(
                WorkspaceMemoryRetrievalEvent.workspace_id == workspace_id
            ),
            limit=limit,
            offset=offset,
        )

    def lifecycle_events(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkspaceMemoryLifecycleEvent], int]:
        return self._page(
            select(WorkspaceMemoryLifecycleEvent).where(
                WorkspaceMemoryLifecycleEvent.workspace_id == workspace_id
            ),
            limit=limit,
            offset=offset,
        )

    def embedding_events(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkspaceMemoryEmbeddingEvent], int]:
        return self._page(
            select(WorkspaceMemoryEmbeddingEvent).where(
                WorkspaceMemoryEmbeddingEvent.workspace_id == workspace_id
            ),
            limit=limit,
            offset=offset,
        )

    def _page(
        self,
        statement: Select[tuple[EvidenceT]],
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[EvidenceT], int]:
        total = int(
            self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        items = self._session.scalars(
            statement.order_by(
                statement.selected_columns.created_at.desc(),
                statement.selected_columns.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
        return list(items), total
