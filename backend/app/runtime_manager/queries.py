from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.runtimes.models import RuntimeCommand, RuntimeEvent, WorkspaceRuntime


class RuntimeControlQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_runtimes(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        statement = select(WorkspaceRuntime).where(
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.status != "deleted",
        )
        count_query = (
            select(func.count())
            .select_from(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status != "deleted",
            )
        )
        if status is not None:
            statement = statement.where(WorkspaceRuntime.status == status)
            count_query = count_query.where(WorkspaceRuntime.status == status)
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                statement.order_by(WorkspaceRuntime.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def get_runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status != "deleted",
            )
        )

    def get_command(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        command_id: UUID,
    ) -> RuntimeCommand | None:
        return self._session.scalar(
            select(RuntimeCommand).where(
                RuntimeCommand.workspace_id == workspace_id,
                RuntimeCommand.workspace_runtime_id == runtime_id,
                RuntimeCommand.id == command_id,
            )
        )

    def list_commands(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeCommand], int]:
        count_query = (
            select(func.count())
            .select_from(RuntimeCommand)
            .where(
                RuntimeCommand.workspace_id == workspace_id,
                RuntimeCommand.workspace_runtime_id == runtime_id,
            )
        )
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                select(RuntimeCommand)
                .where(
                    RuntimeCommand.workspace_id == workspace_id,
                    RuntimeCommand.workspace_runtime_id == runtime_id,
                )
                .order_by(RuntimeCommand.started_at.desc().nullslast(), RuntimeCommand.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def list_events(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[RuntimeEvent], int]:
        count_query = (
            select(func.count())
            .select_from(RuntimeEvent)
            .where(
                RuntimeEvent.workspace_id == workspace_id,
                RuntimeEvent.workspace_runtime_id == runtime_id,
            )
        )
        total = int(self._session.scalar(count_query) or 0)
        items = list(
            self._session.scalars(
                select(RuntimeEvent)
                .where(
                    RuntimeEvent.workspace_id == workspace_id,
                    RuntimeEvent.workspace_runtime_id == runtime_id,
                )
                .order_by(RuntimeEvent.created_at.desc(), RuntimeEvent.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
