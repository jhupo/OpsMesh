"""Durable, bounded process ownership on a workspace Runtime host."""

from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.execution_identity import RuntimeExecutionIdentity
from opsmesh.runtime.instances.models import (
    RuntimeAllocation,
    RuntimeCommand,
    RuntimeHost,
    WorkspaceRuntime,
)

AllocationKind = Literal["run", "mcp", "command"]
FIRST_EXECUTION_UID = 100_000


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
        if runtime.host_id is None:
            raise ValueError("Runtime has no physical host")
        host = self.session.scalar(
            select(RuntimeHost)
            .where(
                RuntimeHost.workspace_id == runtime.workspace_id,
                RuntimeHost.id == runtime.host_id,
                RuntimeHost.status == "running",
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if host is None:
            return None
        existing = self.get(runtime, kind, owner_id)
        if existing is not None:
            return existing
        if not self.available(runtime):
            return None
        occupied = set(
            self.session.scalars(
                select(RuntimeAllocation.execution_uid).where(RuntimeAllocation.host_id == host.id)
            )
        )
        uid = next(
            uid
            for uid in range(FIRST_EXECUTION_UID, FIRST_EXECUTION_UID + host.capacity)
            if uid not in occupied
        )
        allocation = RuntimeAllocation(
            workspace_id=runtime.workspace_id,
            workspace_runtime_id=runtime.id,
            owner_kind=kind,
            owner_id=owner_id,
            host_id=host.id,
            execution_uid=uid,
            network_policy=dict(runtime.network_policy),
        )
        self.session.add(allocation)
        self.session.flush([allocation])
        return allocation

    def available(self, runtime: WorkspaceRuntime) -> bool:
        if self._running_command(runtime) is not None:
            return False
        capacity = runtime.host.capacity if runtime.host is not None else None
        if not isinstance(capacity, int) or not 1 <= capacity <= 128:
            raise ValueError("Runtime execution capacity is invalid")
        count = (
            self.session.scalar(
                select(func.count())
                .select_from(RuntimeAllocation)
                .where(
                    RuntimeAllocation.workspace_id == runtime.workspace_id,
                    RuntimeAllocation.host_id == runtime.host_id,
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
            select(RuntimeHost.id)
            .where(
                RuntimeHost.workspace_id == runtime.workspace_id,
                RuntimeHost.id == runtime.host_id,
            )
            .with_for_update()
        )
        allocation = self.session.scalar(
            select(RuntimeAllocation.id)
            .where(
                RuntimeAllocation.workspace_id == runtime.workspace_id,
                RuntimeAllocation.host_id == runtime.host_id,
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
        statement = (
            select(RuntimeCommand.id)
            .join(WorkspaceRuntime, RuntimeCommand.workspace_runtime_id == WorkspaceRuntime.id)
            .where(
                RuntimeCommand.workspace_id == runtime.workspace_id,
                WorkspaceRuntime.host_id == runtime.host_id,
                RuntimeCommand.status == "running",
            )
        )
        if command_id is not None:
            statement = statement.where(RuntimeCommand.id != command_id)
        return self.session.scalar(statement.limit(1))


def allocation_identity(allocation: RuntimeAllocation) -> RuntimeExecutionIdentity:
    return RuntimeExecutionIdentity(
        allocation.id, allocation.execution_uid, dict(allocation.network_policy)
    )


def run_execution_identity(
    session: Session, runtime: WorkspaceRuntime, owner_id: UUID
) -> RuntimeExecutionIdentity:
    allocation = RuntimeAllocationStore(session).get(runtime, "run", owner_id)
    if allocation is None:
        raise ValueError("Run has no Runtime execution identity")
    return allocation_identity(allocation)
