from uuid import uuid4

from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.workers.placement import worker_owns_runtime_job
from opsmesh.workspaces.management.models import Workspace
from tests.test_run_runtime_environment import _session


def test_worker_only_claims_the_durable_execution_node() -> None:
    session = _session()
    workspace = Workspace(owner_user_id=uuid4(), name="Placement", slug=uuid4().hex)
    session.add(workspace)
    session.flush()
    host = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Remote",
        capabilities={"node_id": "east"},
        status="running",
        connection_status="online",
        docker_container_id="east-container",
        limits={"max_concurrent_executions": 2},
    )
    session.add(host)
    session.flush()
    run = AgentRun(workspace_id=workspace.id, runtime_id=host.id, input={}, status="queued")
    session.add(run)
    session.commit()
    job = JobPayload(
        workspace_id=workspace.id,
        resource_id=run.id,
        job_type=JobType.AGENT_RUN,
        idempotency_key=uuid4().hex,
        routing={"node_id": "west"},
    )
    assert worker_owns_runtime_job(session, job, "east")
    assert not worker_owns_runtime_job(session, job, "west")
    other_host = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="West",
        capabilities={"node_id": "west"},
        status="running",
        connection_status="online",
        docker_container_id="west-container",
        limits={"max_concurrent_executions": 2},
    )
    session.add(other_host)
    session.commit()
    assert worker_owns_runtime_job(session, job, "west")
    run.execution_runtime_id = host.id
    session.commit()
    assert not worker_owns_runtime_job(session, job, "west")
    # An unassigned managed host cannot be executed by guessing a node identity.
    host.capabilities = {}
    session.commit()
    assert not worker_owns_runtime_job(session, job, "east")
