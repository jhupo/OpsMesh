"""Durable, bounded process ownership on a workspace Runtime host."""

from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.models import RuntimeAllocation, RuntimeCommand, WorkspaceRuntime

AllocationKind = Literal["run", "mcp"]


class RuntimeAllocationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(
        self, runtime: WorkspaceRuntime, kind: AllocationKind, owner_id: UUID
    ) -> RuntimeAllocation | None:
        return self.session.scalar(
            select(RuntimeAllocation).where(
                RuntimeAllocation.workspace_id == runtime.workspace_id,
                RuntimeAllocation.workspace_runtime_id == runtime.id,
                RuntimeAllocation.owner_kind == kind,
                RuntimeAllocation.owner_id == owner_id,
            )
        )

    def acquire(
        self, runtime: WorkspaceRuntime, kind: AllocationKind, owner_id: UUID
    ) -> RuntimeAllocation | None:
        # Every admission and lifecycle transition locks the same host row. The lock
        # ends before process startup or any remote I/O; no worker waits holding it.
        host = self.session.scalar(
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == runtime.workspace_id,
                WorkspaceRuntime.id == runtime.id,
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.status.in_(["active", "running"]),
                WorkspaceRuntime.connection_status == "online",
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if host is None:
            return None
        existing = self.get(host, kind, owner_id)
        if existing is not None:
            return existing
        if not self.available(host):
            return None
        allocation = RuntimeAllocation(
            workspace_id=host.workspace_id,
            workspace_runtime_id=host.id,
            owner_kind=kind,
            owner_id=owner_id,
        )
        self.session.add(allocation)
        self.session.flush([allocation])
        return allocation

    def available(self, runtime: WorkspaceRuntime) -> bool:
        if self._running_command(runtime) is not None:
            return False
        capacity = runtime.limits.get("max_concurrent_executions")
        if not isinstance(capacity, int) or not 1 <= capacity <= 128:
            raise ValueError("Runtime execution capacity is invalid")
        count = (
            self.session.scalar(
                select(func.count())
                .select_from(RuntimeAllocation)
                .where(
                    RuntimeAllocation.workspace_id == runtime.workspace_id,
                    RuntimeAllocation.workspace_runtime_id == runtime.id,
                )
            )
            or 0
        )
        return count < capacity

    def release(self, runtime: WorkspaceRuntime, kind: AllocationKind, owner_id: UUID) -> None:
        allocation = self.get(runtime, kind, owner_id)
        if allocation is not None:
            self.session.delete(allocation)
            self.session.flush()

    def require_idle(self, runtime: WorkspaceRuntime, *, command_id: UUID | None = None) -> None:
        self.session.scalar(
            select(WorkspaceRuntime.id)
            .where(
                WorkspaceRuntime.workspace_id == runtime.workspace_id,
                WorkspaceRuntime.id == runtime.id,
            )
            .with_for_update()
        )
        allocation = self.session.scalar(
            select(RuntimeAllocation.id)
            .where(
                RuntimeAllocation.workspace_id == runtime.workspace_id,
                RuntimeAllocation.workspace_runtime_id == runtime.id,
            )
            .limit(1)
        )
        if allocation is not None:
            raise ValueError("Runtime has active task or MCP processes")
        if self._running_command(runtime, command_id=command_id) is not None:
            raise ValueError("Runtime has an active management command")

    def _running_command(
        self, runtime: WorkspaceRuntime, *, command_id: UUID | None = None
    ) -> UUID | None:
        statement = select(RuntimeCommand.id).where(
            RuntimeCommand.workspace_id == runtime.workspace_id,
            RuntimeCommand.workspace_runtime_id == runtime.id,
            RuntimeCommand.status == "running",
        )
        if command_id is not None:
            statement = statement.where(RuntimeCommand.id != command_id)
        return self.session.scalar(statement.limit(1))
