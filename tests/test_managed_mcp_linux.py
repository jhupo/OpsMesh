"""Run the private-socket lifecycle in Linux (also runnable in an isolated container)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from opsmesh.capabilities.mcp.catalog.discovery import McpToolDiscoveryService


def exercise_process() -> dict[str, object]:
    identifier = str(uuid4())
    fixture = str(Path(__file__).parent / "fixtures" / "mcp_persistent_server.py")
    runtime_source = Path(__file__).resolve().parents[1] / "runtime"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(runtime_source), environment.get("PYTHONPATH")))
    )
    with TemporaryDirectory() as directory:
        request_file = Path(directory) / "request.json"

        def request(action: str, **kwargs: object) -> dict:
            request_file.write_text(json.dumps({"id": identifier, "action": action, **kwargs}))
            os.chmod(request_file, 0o600)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "opsmesh_runtime.mcp_process",
                    "--request-file",
                    str(request_file),
                ],
                capture_output=True,
                text=True,
                timeout=80,
                env=environment,
            )
            assert "smoke-only-secret" not in result.stderr
            assert result.stdout, result.stderr
            return json.loads(result.stdout)

        try:
            assert (
                request(
                    "start",
                    server={
                        "command": sys.executable,
                        "args": [fixture],
                        "env": {"TEST_PASSWORD": "smoke-only-secret"},
                        "restart_policy": {
                            "max_restarts": 2,
                            "initial_backoff_seconds": 1,
                            "max_backoff_seconds": 2,
                            "stable_after_seconds": 300,
                        },
                    },
                )["status"]
                == "running"
            )
            tools = request("discover")["tools"]
            assert len(tools) == 2
            for tool in tools:
                McpToolDiscoveryService._normalize_tool(tool)
            first = request("call", name="count", arguments={})["structuredContent"]
            second = request("call", name="count", arguments={})["structuredContent"]
            assert first["pid"] == second["pid"] and second["calls"] == 2
            assert second["configured"] is True
            # Repeated start must not spawn a duplicate process or reset its state.
            assert request("start", server={})["status"] == "running"
            assert request("call", name="count", arguments={})["structuredContent"]["calls"] == 3
            assert "error" in request("call", name="crash", arguments={})
            deadline = time.monotonic() + 30
            while request("status")["status"] != "running":
                assert time.monotonic() < deadline
                time.sleep(0.2)
            recovered = request("call", name="count", arguments={})["structuredContent"]
            assert recovered["pid"] != first["pid"] and recovered["calls"] == 1
            return {"same_process_reused": True, "restart_recovered": True, "env_injected": True}
        finally:
            assert request("stop")["status"] in {"stopping", "stopped"}
            deadline = time.monotonic() + 15
            while request("status")["status"] != "stopped":
                assert time.monotonic() < deadline
                time.sleep(0.2)


def test_linux_private_socket_process_lifecycle() -> None:
    import pytest

    if sys.platform != "linux":
        pytest.skip("The isolated Runtime supervisor uses Linux private sockets")
    exercise_process()


if __name__ == "__main__":
    print(json.dumps(exercise_process()))
