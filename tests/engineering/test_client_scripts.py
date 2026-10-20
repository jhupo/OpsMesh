"""Operator-facing script contracts use current API routes and explicit external tests."""

import argparse
import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.errors import TransportError
from opsmesh_sdk.transport.http import HttpTransport

from scripts import configuration_client, openai_gateway_smoke

CONFIGURATION = UUID("11111111-1111-1111-1111-111111111111")
SESSION = UUID("22222222-2222-2222-2222-222222222222")
RUN = UUID("33333333-3333-3333-3333-333333333333")


def run_payload(status):
    return {
        "run_id": str(RUN),
        "session_id": str(SESSION),
        "sequence": 1,
        "status": status,
        "output": "done" if status == "succeeded" else None,
        "error_code": None,
        "revision": 3,
        "created_at": "2026-10-20T00:00:00Z",
    }


def event(sequence, kind):
    data = json.dumps({"sequence": sequence, "kind": kind, "payload": {}})
    return f"id: {sequence}\ndata: {data}\n\n".encode()


def arguments(**overrides):
    return argparse.Namespace(
        **{
            "configuration": CONFIGURATION,
            "run": None,
            "revision": None,
            "session": None,
            "request_key": "submit-once",
            "message": "hello",
            "timeout": 3,
            "after": 0,
            **overrides,
        }
    )


def test_configuration_submission_uses_private_default_session_and_resumes_only_gets(monkeypatch):
    requests = []
    cursors = []

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield event(1, "running")
            raise httpx.ReadError("Interrupted after the first durable event")

    def handler(request):
        requests.append(request)
        assert request.headers["X-Session-ID"] == "login-session"
        path = request.url.path
        if path == f"/custom/orchestration/configurations/{CONFIGURATION}":
            return httpx.Response(
                200,
                json={
                    "id": str(CONFIGURATION),
                    "owner_user_id": str(CONFIGURATION),
                    "definition_revision_id": str(CONFIGURATION),
                    "root_directory_id": str(CONFIGURATION),
                    "name": "example",
                    "status": "active",
                    "revision": 7,
                },
            )
        if request.method == "POST":
            assert path == f"/custom/orchestration/configurations/{CONFIGURATION}/runs"
            assert dict(request.url.params) == {"idempotency_key": "submit-once"}
            assert json.loads(request.content) == {"configuration_revision": 7, "input": "hello"}
            return httpx.Response(202, json=run_payload("queued"))
        if path.endswith("/events/stream"):
            cursors.append(request.url.params["after"])
            if len(cursors) == 1:
                return httpx.Response(200, stream=InterruptedStream())
            return httpx.Response(200, content=event(2, "succeeded"))
        assert path == f"/custom/orchestration/runs/{RUN}"
        return httpx.Response(200, json=run_payload("succeeded"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            sdk = PlatformClient(
                "https://platform.test",
                session_id="login-session",
                api_prefix="/custom",
                transport=HttpTransport("https://platform.test", client=http),
            )
            monkeypatch.setattr(configuration_client, "PlatformClient", lambda *args, **kwargs: sdk)
            return await configuration_client.run(arguments())

    monkeypatch.setenv("OPSMESH_SESSION_ID", "login-session")
    assert asyncio.run(scenario()) == 0
    assert cursors == ["0", "1"]
    assert sum(request.method == "POST" for request in requests) == 1


def test_resuming_an_existing_run_never_creates_a_session_or_run(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/orchestration/runs/{RUN}"
        return httpx.Response(200, json=run_payload("succeeded"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            sdk = PlatformClient(
                "https://platform.test",
                session_id="login-session",
                transport=HttpTransport("https://platform.test", client=http),
            )
            monkeypatch.setattr(configuration_client, "PlatformClient", lambda *args, **kwargs: sdk)
            return await configuration_client.run(arguments(configuration=None, run=RUN))

    monkeypatch.setenv("OPSMESH_SESSION_ID", "login-session")
    assert asyncio.run(scenario()) == 0


def test_rejected_stream_exits_instead_of_retrying_authorization(monkeypatch):
    def handler(request):
        return httpx.Response(403)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            sdk = PlatformClient(
                "https://platform.test",
                transport=HttpTransport("https://platform.test", client=http),
            )
            api = configuration_client.create_orchestration_client(sdk)
            with pytest.raises(TransportError):
                await configuration_client.follow_run(api, RUN)

    asyncio.run(scenario())


@pytest.fixture
def smoke_environment(monkeypatch):
    settings = {
        "OPSMESH_TEST_MODEL_API_KEY": "environment-only-key",
        "OPSMESH_TEST_MODEL_BASE_URL": "http://localhost:1234/v1",
        "OPSMESH_TEST_MODEL_NAME": "test-model",
        "OPSMESH_TEST_DATABASE_URL": "postgresql+psycopg://test@localhost/isolated",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    return settings


def test_model_smoke_dry_run_never_calls_external_model(smoke_environment, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("A dry run must not start the external model test")

    monkeypatch.setattr(openai_gateway_smoke.subprocess, "run", forbidden)
    assert openai_gateway_smoke.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert openai_gateway_smoke.MODEL_TEST in output
    assert smoke_environment["OPSMESH_TEST_MODEL_API_KEY"] not in output
    assert smoke_environment["OPSMESH_TEST_DATABASE_URL"] not in output


def test_model_smoke_requires_explicit_external_opt_in(smoke_environment):
    with pytest.raises(SystemExit, match="--allow-external-provider-call"):
        openai_gateway_smoke.main([])


def test_model_smoke_selects_current_real_model_case_and_passes_env_only(
    smoke_environment, monkeypatch
):
    def capture(command, *, env, cwd, check):
        assert command[3] == openai_gateway_smoke.MODEL_TEST
        assert env["OPSMESH_TEST_TEMPORAL_LOCAL"] == "1"
        assert all(env[name] == value for name, value in smoke_environment.items())
        assert cwd == openai_gateway_smoke.ROOT
        assert check is False
        assert "environment-only-key" not in " ".join(command)
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(openai_gateway_smoke.subprocess, "run", capture)
    assert openai_gateway_smoke.main(["--allow-external-provider-call"]) == 17
