from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from backend.app.capabilities.mcp.transport.payloads import stdio_sdk_request
from runtime.opsmesh_runtime import mcp_stdio_client


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


def test_stdio_sdk_client_cli_reads_credentials_from_transient_file(tmp_path: Path) -> None:
    server_path = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    request = stdio_sdk_request(
        command=[sys.executable, str(server_path)],
        tool_name="environment_configured",
        arguments={"name": "MCP_API_KEY"},
        timeout_seconds=5,
        environment={"MCP_API_KEY": "runtime-secret"},
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "runtime.opsmesh_runtime.mcp_stdio_client",
            "--request-file",
            str(request_path),
        ],
        capture_output=True,
        check=False,
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
    assert report["contract_version"] == 2
    assert report["sdk_package"] == "openai-agents"
    assert report["stdio_server"] == "available"
    assert isinstance(report["sdk_version"], str)


def test_stdio_sdk_client_cli_reads_request_file(monkeypatch, capsys, tmp_path: Path) -> None:
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
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    exit_code = mcp_stdio_client.main(["--request-file", str(request_path)])

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
    request["contract_version"] = 1

    try:
        asyncio.run(mcp_stdio_client.execute_request(request))
    except ValueError as exc:
        assert "unsupported MCP stdio request contract version" in str(exc)
    else:
        raise AssertionError("Expected an unknown request contract version to be rejected")
