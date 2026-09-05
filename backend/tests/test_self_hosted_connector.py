from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from backend.app.capabilities.mcp_adapter_payloads import (
    MCP_PYTHON_SDK_PACKAGE,
    MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
    MCP_STDIO_CONTRACT_VERSION,
    stdio_sdk_request,
)
from runtime.opsmesh_runtime.connector_api import HttpMcpJobApi
from runtime.opsmesh_runtime.connector_models import (
    ConnectorApiError,
    McpJob,
    McpJobCompletion,
)
from runtime.opsmesh_runtime.connector_state import ConnectorStateStore
from runtime.opsmesh_runtime.connector_worker import SelfHostedMcpConnector


def test_connector_executes_real_official_mcp_server_and_completes(tmp_path: Path) -> None:
    server_path = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    job = _job(
        command=[sys.executable, str(server_path)],
        arguments={"message": "self-hosted-ready"},
    )
    api = _RecordingApi(job)

    with ConnectorStateStore(tmp_path / "connector.sqlite3") as state:
        outcome = SelfHostedMcpConnector(api=api, state=state).run_once()
        pending = state.next_pending()

    assert outcome == "completed"
    assert api.claimed == [job.id]
    assert pending is None
    assert len(api.completions) == 1
    completion = api.completions[0][1]
    assert completion.status == "completed"
    assert completion.response_payload is not None
    assert completion.response_payload["structuredContent"] == {
        "message": "self-hosted-ready"
    }


def test_connector_replays_recorded_result_after_restart_without_reexecution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "connector.sqlite3"
    job = _job()
    completion = McpJobCompletion.completed({"structuredContent": {"ok": True}})
    with ConnectorStateStore(path) as state:
        state.remember_claimed(job)
        state.mark_executing(job.id)
        state.mark_completion(job.id, completion)

    executions: list[dict[str, object]] = []

    async def executor(request: dict[str, object]) -> dict[str, object]:
        executions.append(request)
        return {}

    api = _RecordingApi()
    with ConnectorStateStore(path) as state:
        outcome = SelfHostedMcpConnector(
            api=api,
            state=state,
            executor=executor,
        ).run_once()

    assert outcome == "recovered"
    assert executions == []
    assert api.completions == [(job.id, completion)]


def test_connector_marks_interrupted_execution_failed_without_reexecution(tmp_path: Path) -> None:
    path = tmp_path / "connector.sqlite3"
    job = _job()
    with ConnectorStateStore(path) as state:
        state.remember_claimed(job)
        state.mark_executing(job.id)

    executions: list[dict[str, object]] = []

    async def executor(request: dict[str, object]) -> dict[str, object]:
        executions.append(request)
        return {}

    api = _RecordingApi()
    with ConnectorStateStore(path) as state:
        outcome = SelfHostedMcpConnector(
            api=api,
            state=state,
            executor=executor,
        ).run_once()

    assert outcome == "recovered"
    assert executions == []
    completion = api.completions[0][1]
    assert completion.status == "failed"
    assert completion.error_payload is not None
    assert completion.error_payload["code"] == "self_hosted_mcp_execution_interrupted"


def test_connector_rejects_invalid_job_contract_and_reports_sanitized_failure(
    tmp_path: Path,
) -> None:
    job = _job()
    job.request_payload["contract_version"] = 2
    api = _RecordingApi(job)
    executions: list[dict[str, object]] = []

    async def executor(request: dict[str, object]) -> dict[str, object]:
        executions.append(request)
        return {}

    with ConnectorStateStore(tmp_path / "connector.sqlite3") as state:
        outcome = SelfHostedMcpConnector(
            api=api,
            state=state,
            executor=executor,
        ).run_once()

    assert outcome == "failed"
    assert executions == []
    completion = api.completions[0][1]
    assert completion.error_payload == {
        "code": "self_hosted_mcp_contract_invalid",
        "message": "Self-hosted MCP job contract is invalid.",
        "retryable": False,
    }


def test_connector_sanitizes_sdk_execution_failure(tmp_path: Path) -> None:
    job = _job(arguments={"secret": "must-not-escape"})
    api = _RecordingApi(job)

    async def executor(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("must-not-escape")

    with ConnectorStateStore(tmp_path / "connector.sqlite3") as state:
        outcome = SelfHostedMcpConnector(
            api=api,
            state=state,
            executor=executor,
        ).run_once()

    assert outcome == "failed"
    error = api.completions[0][1].error_payload
    assert error is not None
    assert error["code"] == "self_hosted_mcp_execution_failed"
    assert "must-not-escape" not in str(error)


def test_http_connector_api_uses_runtime_header_and_expected_endpoints() -> None:
    job = _job()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"status": "online"})
        if request.url.path.endswith("/next"):
            return httpx.Response(
                200,
                json={"id": str(job.id), "request_payload": job.request_payload},
            )
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json={"id": str(job.id), "status": "claimed"})
        return httpx.Response(200, json={"id": str(job.id), "status": "completed"})

    with HttpMcpJobApi(
        api_url="http://127.0.0.1:8000/api/v1",
        credential="runtime-secret",
        transport=httpx.MockTransport(handler),
    ) as api:
        api.heartbeat({})
        assert api.poll_mcp_job() == job
        api.claim_mcp_job(job.id)
        api.complete_mcp_job(job.id, McpJobCompletion.completed({"ok": True}))

    assert [request.url.path for request in requests] == [
        "/api/v1/self-hosted/heartbeat",
        "/api/v1/self-hosted/mcp-jobs/next",
        f"/api/v1/self-hosted/mcp-jobs/{job.id}/claim",
        f"/api/v1/self-hosted/mcp-jobs/{job.id}/complete",
    ]
    assert all(
        request.headers["X-Runtime-Authorization"] == "Bearer runtime-secret"
        for request in requests
    )


def test_http_connector_api_rejects_plaintext_remote_url() -> None:
    try:
        HttpMcpJobApi(api_url="http://example.test/api/v1", credential="runtime-secret")
    except ValueError as exc:
        assert "must use HTTPS" in str(exc)
        assert "runtime-secret" not in str(exc)
    else:
        raise AssertionError("Expected a plaintext remote API URL to be rejected")


def test_http_connector_api_does_not_expose_response_body_or_credential() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="runtime-secret server-secret")

    with HttpMcpJobApi(
        api_url="https://example.test/api/v1",
        credential="runtime-secret",
        transport=httpx.MockTransport(handler),
    ) as api:
        try:
            api.poll_mcp_job()
        except ConnectorApiError as exc:
            assert "500" in str(exc)
            assert "runtime-secret" not in str(exc)
            assert "server-secret" not in str(exc)
        else:
            raise AssertionError("Expected the failed control-plane request to be normalized")


class _RecordingApi:
    def __init__(self, job: McpJob | None = None) -> None:
        self._job = job
        self.claimed: list[UUID] = []
        self.completions: list[tuple[UUID, McpJobCompletion]] = []

    def poll_mcp_job(self) -> McpJob | None:
        job = self._job
        self._job = None
        return job

    def claim_mcp_job(self, job_id: UUID) -> None:
        self.claimed.append(job_id)

    def complete_mcp_job(self, job_id: UUID, completion: McpJobCompletion) -> None:
        self.completions.append((job_id, completion))


def _job(
    *,
    command: list[str] | None = None,
    arguments: dict[str, object] | None = None,
) -> McpJob:
    request = stdio_sdk_request(
        command=command or ["mcp-server"],
        tool_name="echo",
        arguments=arguments or {},
        timeout_seconds=5,
    )
    return McpJob(
        id=uuid4(),
        request_payload={
            "contract_version": MCP_STDIO_CONTRACT_VERSION,
            "transport": "stdio",
            "sdk": {
                "package": MCP_PYTHON_SDK_PACKAGE,
                "entrypoint": MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
            },
            "request": request,
        },
    )
