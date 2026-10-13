from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.models import (
    RuntimeCommand,
    RuntimeEvent,
    WorkspaceRuntime,
)


def list_runtimes(
    session: Session,
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
    total = int(session.scalar(count_query) or 0)
    items = list(
        session.scalars(
            statement.order_by(WorkspaceRuntime.created_at.desc()).limit(limit).offset(offset)
        )
    )
    return items, total


def get_runtime(session: Session, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime | None:
    return session.scalar(
        select(WorkspaceRuntime).where(
            WorkspaceRuntime.id == runtime_id,
            WorkspaceRuntime.workspace_id == workspace_id,
            WorkspaceRuntime.status != "deleted",
        )
    )


def list_commands(
    session: Session,
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
    total = int(session.scalar(count_query) or 0)
    items = list(
        session.scalars(
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
    session: Session,
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
    total = int(session.scalar(count_query) or 0)
    items = list(
        session.scalars(
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
