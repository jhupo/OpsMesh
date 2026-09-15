from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.runtime.environment.models import RuntimeLease, WorkspaceRuntime
from backend.app.runtime.environment.pool.leases import RuntimePoolLeaseStore
from backend.app.runtime.environment.pool.policy import pool_policy_matches, runtime_pool_key

MANAGED_RUNTIME_PROVIDERS = frozenset({"docker", "cloud_docker"})


@dataclass(frozen=True, slots=True)
class RuntimePoolAcquisition:
    member: WorkspaceRuntime
    lease: RuntimeLease


class RuntimePoolService:
    """Select and exclusively lease a reusable member that matches the parent policy."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._leases = RuntimePoolLeaseStore(session)

    def acquire(
        self,
        parent: WorkspaceRuntime,
        run: AgentRun,
    ) -> RuntimePoolAcquisition | None:
        statement = (
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == parent.workspace_id,
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.runtime_provider.in_(MANAGED_RUNTIME_PROVIDERS),
                WorkspaceRuntime.status.in_(["active", "running"]),
                WorkspaceRuntime.connection_status == "online",
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.execution_pool_member_id.is_(None),
                WorkspaceRuntime.runtime_template_id == parent.runtime_template_id,
                WorkspaceRuntime.runtime_space_id == parent.runtime_space_id,
            )
            .order_by(WorkspaceRuntime.updated_at.asc(), WorkspaceRuntime.created_at.asc())
            .with_for_update(skip_locked=True)
        )
        for member in self._session.scalars(statement):
            if (
                runtime_pool_key(member) != runtime_pool_key(parent)
                or not pool_policy_matches(parent, member)
                or not member.docker_container_id
            ):
                continue
            lease = self._leases.acquire(
                member,
                run_id=run.id,
                metadata={
                    "mode": "pooled",
                    "parent_runtime_id": str(parent.id),
                    "pool_key": runtime_pool_key(parent),
                },
            )
            if lease is not None:
                return RuntimePoolAcquisition(member, lease)
        return None

    def release(self, member: WorkspaceRuntime, run: AgentRun) -> RuntimeLease | None:
        return self._leases.release(member, run_id=run.id)
