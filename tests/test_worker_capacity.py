from uuid import uuid4

from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.workers.capacity import worker_can_run_job
from opsmesh.runtime.workers.models import WorkerRunnerConfig


def _job(job_type: JobType, routing: dict[str, object]) -> JobPayload:
    workspace_id = uuid4()
    return JobPayload(
        workspace_id=workspace_id,
        job_type=job_type,
        resource_id=uuid4(),
        idempotency_key=f"{job_type}:{workspace_id}",
        routing=routing,
    )


def test_worker_config_declares_distributed_capacity_contract() -> None:
    config = WorkerRunnerConfig(
        worker_id="worker-east",
        concurrency=8,
        task_concurrency=6,
        mcp_concurrency=2,
        region="cn-east",
        capabilities=("code.execute",),
        runtime_modes=("shared",),
        cpu_count=4,
        memory_mb=8192,
    )

    assert config.capacity() == {
        "max_jobs": 8,
        "task_slots": 6,
        "mcp_slots": 2,
        "region": "cn-east",
        "capabilities": ["code.execute"],
        "runtime_modes": ["shared"],
        "cpu_count": 4,
        "memory_mb": 8192,
    }


def test_worker_capacity_enforces_region_resources_and_mcp_slots() -> None:
    capacity = {
        "worker_type": "cloud",
        "region": "cn-east",
        "capabilities": ["code.execute"],
        "runtime_modes": ["shared"],
        "task_slots": 4,
        "mcp_slots": 1,
        "cpu_count": 4,
        "memory_mb": 8192,
        "running_jobs_by_type": {"mcp.process_control": 1},
    }
    routing = {
        "regions": ["cn-east"],
        "capabilities": ["code.execute"],
        "runtime_modes": ["shared"],
        "resource_requirements": {"cpu_count": 2, "memory_mb": 4096},
    }

    assert worker_can_run_job(_job(JobType.AGENT_RUN, routing), capacity)
    assert not worker_can_run_job(
        _job(JobType.AGENT_RUN, routing | {"regions": ["cn-west"]}), capacity
    )
    assert not worker_can_run_job(_job(JobType.MCP_PROCESS_CONTROL, routing), capacity)
