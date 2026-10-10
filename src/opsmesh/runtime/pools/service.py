from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore
from opsmesh.runtime.instances.models import RuntimeAllocation, WorkspaceRuntime
from opsmesh.runtime.pools.policy import pool_policy_matches, runtime_pool_key

MANAGED_RUNTIME_PROVIDER = "cloud_docker"


@dataclass(frozen=True, slots=True)
class RuntimePoolAcquisition:
    member: WorkspaceRuntime
    allocation: RuntimeAllocation


class RuntimePoolService:
    """Select a host with a free process slot and the authorized parent policy."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._allocations = RuntimeAllocationStore(session)

    def acquire(
        self,
        parent: WorkspaceRuntime,
        run: AgentRun,
    ) -> RuntimePoolAcquisition | None:
        hosts = self.hosts(parent)
        existing = self._session.scalar(
            select(RuntimeAllocation).where(
                RuntimeAllocation.workspace_id == run.workspace_id,
                RuntimeAllocation.owner_kind == "run",
                RuntimeAllocation.owner_id == run.id,
            )
        )
        if existing is not None:
            for member in hosts:
                if member.id == existing.workspace_runtime_id:
                    return RuntimePoolAcquisition(member, existing)
            raise ValueError("Run's Runtime host no longer matches its placement policy")
        for member in hosts:
            allocation = self._allocations.acquire(member, "run", run.id)
            if allocation is not None:
                return RuntimePoolAcquisition(member, allocation)
        return None

    def hosts(self, parent: WorkspaceRuntime) -> list[WorkspaceRuntime]:
        statement = (
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == parent.workspace_id,
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.runtime_provider == MANAGED_RUNTIME_PROVIDER,
                WorkspaceRuntime.status.in_(["active", "running"]),
                WorkspaceRuntime.connection_status == "online",
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.runtime_template_id == parent.runtime_template_id,
                WorkspaceRuntime.runtime_space_id == parent.runtime_space_id,
            )
            .order_by(WorkspaceRuntime.updated_at.asc(), WorkspaceRuntime.created_at.asc())
        )
        return [
            member
            for member in self._session.scalars(statement)
            if runtime_pool_key(member) == runtime_pool_key(parent)
            and pool_policy_matches(parent, member)
            and member.docker_container_id
        ]
