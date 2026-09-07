from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.app.capabilities.mcp_adapter_payloads import stdio_sdk_request
from runtime.opsmesh_runtime import mcp_stdio_client


def test_stdio_sdk_client_uses_official_stdio_transport_and_session(monkeypatch) -> None:
    transport = _FakeTransport()
    session = _FakeSession()
    parameters: list[Any] = []

    def stdio_client(server_parameters: object) -> _FakeTransport:
        parameters.append(server_parameters)
        return transport

    monkeypatch.setattr(mcp_stdio_client, "stdio_client", stdio_client)
    monkeypatch.setattr(mcp_stdio_client, "ClientSession", lambda *_: session)

    request = stdio_sdk_request(
        command=["mcp-server", "--stdio"],
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
        timeout_seconds=7,
        environment={"MCP_API_KEY": "runtime-secret"},
    )

    result = asyncio.run(mcp_stdio_client.execute_request(request))

    assert result == {"structuredContent": {"ok": True}}
    assert parameters[0].env == {"MCP_API_KEY": "runtime-secret"}
    assert session.initialized is True
    assert session.tool_call == {
        "name": "generate_image",
        "arguments": {"prompt": "mountain"},
        "timeout_seconds": 7,
    }


def test_stdio_sdk_client_calls_real_official_mcp_server() -> None:
    server_path = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    request = stdio_sdk_request(
        command=[sys.executable, str(server_path)],
        tool_name="echo",
        arguments={"message": "runtime-ready"},
        timeout_seconds=5,
    )

    result = asyncio.run(mcp_stdio_client.execute_request(request))

    assert result["isError"] is False
    assert result["structuredContent"] == {"message": "runtime-ready"}


def test_stdio_sdk_client_cli_streams_credentials_to_real_server_via_stdin() -> None:
    server_path = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    request = stdio_sdk_request(
        command=[sys.executable, str(server_path)],
        tool_name="environment_configured",
        arguments={"name": "MCP_API_KEY"},
        timeout_seconds=5,
        environment={"MCP_API_KEY": "runtime-secret"},
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "runtime.opsmesh_runtime.mcp_stdio_client",
            "--request-stdin",
        ],
        capture_output=True,
        check=False,
        input=json.dumps(request),
        text=True,
        timeout=10,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert result["structuredContent"] == {"configured": True}
    assert "runtime-secret" not in str(completed.args)
    assert "runtime-secret" not in completed.stderr


def test_stdio_sdk_client_reports_runtime_capability() -> None:
    report = mcp_stdio_client.capability_report()

    assert report["status"] == "ready"
    assert report["contract_version"] == 1
    assert report["sdk_package"] == "mcp"
    assert report["stdio_client"] == "available"
    assert report["client_session"] == "available"
    assert isinstance(report["sdk_version"], str)


def test_stdio_sdk_client_cli_reads_request_from_stdin(monkeypatch, capsys) -> None:
    request = stdio_sdk_request(
        command=["mcp-server"],
        tool_name="echo",
        arguments={"message": "stdin"},
        timeout_seconds=5,
        environment={"MCP_API_KEY": "runtime-secret"},
    )
    observed: list[dict[str, object]] = []

    async def execute_request(payload: dict[str, object]) -> dict[str, object]:
        observed.append(payload)
        return {"structuredContent": {"ok": True}}

    monkeypatch.setattr(mcp_stdio_client, "execute_request", execute_request)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))

    exit_code = mcp_stdio_client.main(["--request-stdin"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert observed == [request]
    assert output == {"structuredContent": {"ok": True}}


def test_stdio_sdk_client_rejects_unknown_contract_version() -> None:
    request = stdio_sdk_request(
        command=["mcp-server"],
        tool_name="echo",
        arguments={},
        timeout_seconds=5,
    )
    request["contract_version"] = 2

    try:
        asyncio.run(mcp_stdio_client.execute_request(request))
    except ValueError as exc:
        assert "unsupported MCP stdio request contract version" in str(exc)
    else:
        raise AssertionError("Expected an unknown request contract version to be rejected")


def test_stdio_sdk_client_times_out_during_session_initialization(monkeypatch) -> None:
    monkeypatch.setattr(mcp_stdio_client, "stdio_client", lambda _: _FakeTransport())
    monkeypatch.setattr(mcp_stdio_client, "ClientSession", lambda *_: _HangingSession())
    request = stdio_sdk_request(
        command=["mcp-server"],
        tool_name="echo",
        arguments={},
        timeout_seconds=1,
    )

    try:
        asyncio.run(mcp_stdio_client.execute_request(request))
    except TimeoutError:
        pass
    else:
        raise AssertionError("Expected MCP session initialization to time out")


class _FakeTransport:
    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeSession:
    initialized = False
    tool_call: dict[str, object] | None = None

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def initialize(self) -> None:
        self.initialized = True

    async def call_tool(
        self,
        name: str,
        *,
        arguments: dict[str, object],
        read_timeout_seconds: object,
    ) -> _FakeResult:
        self.tool_call = {
            "name": name,
            "arguments": arguments,
            "timeout_seconds": read_timeout_seconds.total_seconds(),
        }
        return _FakeResult()


class _HangingSession(_FakeSession):
    async def initialize(self) -> None:
        await asyncio.sleep(2)


class _FakeResult:
    def model_dump(self, **_: object) -> dict[str, object]:
        return {"structuredContent": {"ok": True}}
