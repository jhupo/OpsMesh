from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.orchestration.run_execution import (
    RunExecutionDependencies,
    RunExecutionService,
)
from backend.app.workers.job_handlers.agent_run import AgentRunJobHandler
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.jobs import JobPayload, JobType


def test_agent_run_handler_passes_worker_docker_client_to_execution_service(monkeypatch) -> None:
    docker_client = object()
    captured: dict[str, object] = {}

    class _FakeOrchestration:
        def __init__(self, session: object, queue: object) -> None:
            captured["session"] = session
            captured["queue"] = queue

        def _run_lifecycle(self) -> object:
            return "lifecycle"

    class _FakeExecution:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_agent_sync(self, job: JobPayload) -> None:
            captured["job"] = job

    monkeypatch.setattr(
        "backend.app.workers.job_handlers.agent_run.RunOrchestrationService",
        _FakeOrchestration,
    )
    monkeypatch.setattr(
        "backend.app.workers.job_handlers.agent_run.RunExecutionService",
        _FakeExecution,
    )
    context = WorkerJobHandlerContext(
        session=object(),
        queue=object(),
        runtime_docker_client=docker_client,
    )
    job = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="runtime-wiring",
    )

    AgentRunJobHandler(context).handle(job)

    assert captured["docker_client"] is docker_client
    assert captured["dependencies"] == RunExecutionDependencies(lifecycle="lifecycle")
    assert captured["job"] is job


def test_run_execution_request_builder_retains_docker_client() -> None:
    docker_client = object()
    service = RunExecutionService(
        session=object(),
        dependencies=RunExecutionDependencies(lifecycle=object()),
        settings=Settings(environment="test"),
        docker_client=docker_client,
    )

    assert service._request_builder().docker_client is docker_client
