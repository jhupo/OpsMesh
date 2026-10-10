"""Resolve execution placement from durable, workspace-scoped resources."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.models import McpDeployment
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.pools.service import RuntimePoolService
from opsmesh.runtime.queues.contracts import JobPayload, JobType


def worker_owns_runtime_job(session: Session, job: JobPayload, node_id: str) -> bool:
    runtime_id: UUID | None = None
    if job.job_type == JobType.AGENT_RUN:
        run = session.get(AgentRun, job.resource_id)
        if run is None or run.workspace_id != job.workspace_id:
            return True  # The handler records the missing/unauthorized resource failure.
        runtime_id = run.execution_runtime_id or run.runtime_id
        if run.execution_runtime_id is None and run.runtime_id is not None:
            parent = session.get(WorkspaceRuntime, run.runtime_id)
            if (
                parent is not None
                and parent.workspace_id == job.workspace_id
                and (
                    parent.execution_mode == "shared" and parent.runtime_provider == "cloud_docker"
                )
            ):
                return any(
                    RuntimeAllocationStore(session).available(host)
                    for host in RuntimePoolService(session, node_id=node_id).hosts(parent)
                )
    elif job.job_type == JobType.MCP_PROCESS_CONTROL:
        deployment = session.get(McpDeployment, job.resource_id)
        if deployment is None or deployment.workspace_id != job.workspace_id:
            return True
        runtime_id = deployment.runtime_id
    elif job.job_type == JobType.RUNTIME_CONTROL:
        if job.routing.get("action") == "create":
            return True
        runtime_id = job.resource_id
    if runtime_id is None:
        return True
    runtime = session.get(WorkspaceRuntime, runtime_id)
    if runtime is None or runtime.workspace_id != job.workspace_id:
        return True
    if runtime.runtime_provider != "cloud_docker":
        return True
    return runtime.capabilities.get("node_id") == node_id
