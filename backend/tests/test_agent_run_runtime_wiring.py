from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.domains.orchestration.runs.execution import (
    RunExecutionDependencies,
    RunExecutionService,
)
from backend.app.runtime.environment.backends.factory import build_runtime_backend_registry
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.handlers.agent_run import AgentRunJobHandler
from backend.app.runtime.workers.handlers.context import WorkerJobHandlerContext


def test_agent_run_handler_passes_worker_docker_client_to_execution_service(monkeypatch) -> None:
    docker_client = object()
    runtime_backends = build_runtime_backend_registry(None)
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
        "backend.app.runtime.workers.handlers.agent_run.RunOrchestrationService",
        _FakeOrchestration,
    )
    monkeypatch.setattr(
        "backend.app.runtime.workers.handlers.agent_run.RunExecutionService",
        _FakeExecution,
    )
    context = WorkerJobHandlerContext(
        session=object(),
        runtime_backends=runtime_backends,
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
    assert captured["dependencies"] == RunExecutionDependencies(
        lifecycle="lifecycle",
        runtime_backends=runtime_backends,
    )
    assert captured["job"] is job


def test_run_execution_request_builder_retains_docker_client() -> None:
    docker_client = object()
    runtime_backends = build_runtime_backend_registry(None)
    service = RunExecutionService(
        session=object(),
        dependencies=RunExecutionDependencies(
            lifecycle=object(),
            runtime_backends=runtime_backends,
        ),
        settings=Settings(environment="test"),
        docker_client=docker_client,
    )

    assert service._request_builder().docker_client is docker_client
    assert service._request_builder().runtime_backends is runtime_backends
