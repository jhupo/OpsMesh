"""Validate the published runtime image with the official MCP SDK."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

MCP_SDK_PACKAGE = "mcp"
MCP_SDK_STDIO_ENTRYPOINT = "mcp.client.stdio.stdio_client"
MCP_CONTRACT_VERSION = 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Runtime image tag or digest")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("backend/tests/fixtures/mcp_stdio_server.py"),
        help="FastMCP stdio fixture mounted into the image for the probe",
    )
    args = parser.parse_args(argv)
    fixture = args.fixture.resolve()
    if not fixture.is_file():
        raise SystemExit(f"MCP stdio fixture does not exist: {fixture}")

    _run(["docker", "image", "inspect", args.image], description="inspect runtime image")
    user = _run(
        ["docker", "image", "inspect", "--format", "{{.Config.User}}", args.image],
        description="inspect runtime image user",
    ).stdout.strip()
    if user != "opsmesh-runtime":
        raise SystemExit(f"runtime image must run as opsmesh-runtime, got {user!r}")

    check = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            args.image,
            "python",
            "-m",
            "opsmesh_runtime.mcp_stdio_client",
            "--check",
        ],
        description="probe runtime MCP SDK capability",
    )
    report = _json_object(check.stdout, "runtime capability report")
    if report.get("status") != "ready":
        raise SystemExit("runtime capability report is not ready")
    if report.get("sdk_package") != MCP_SDK_PACKAGE:
        raise SystemExit("runtime capability report has an unexpected SDK package")

    with tempfile.TemporaryDirectory(prefix="opsmesh-runtime-probe-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_text(
            json.dumps(
                {
                    "contract_version": MCP_CONTRACT_VERSION,
                    "client": {
                        "package": MCP_SDK_PACKAGE,
                        "entrypoint": MCP_SDK_STDIO_ENTRYPOINT,
                    },
                    "server": {
                        "command": "python",
                        "args": ["/fixtures/mcp_stdio_server.py"],
                    },
                    "tool": {
                        "name": "echo",
                        "arguments": {"message": "runtime-image-ready"},
                        "timeout_seconds": 10,
                    },
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        result = _run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,src={fixture.parent},dst=/fixtures,readonly",
                "--mount",
                f"type=bind,src={request_path},dst=/tmp/request.json,readonly",
                args.image,
                "python",
                "-m",
                "opsmesh_runtime.mcp_stdio_client",
                "--request-file",
                "/tmp/request.json",
            ],
            description="execute FastMCP stdio probe in runtime image",
        )
        payload = _json_object(result.stdout, "runtime MCP result")
        if payload.get("structuredContent") != {"message": "runtime-image-ready"}:
            raise SystemExit("runtime MCP probe returned an unexpected result")
    return 0


def _run(command: list[str], *, description: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-2_000:]
        raise SystemExit(f"{description} failed ({completed.returncode}): {detail}")
    return completed


def _json_object(raw: str, description: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{description} was not valid JSON") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{description} was not a JSON object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
