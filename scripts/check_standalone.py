"""Exercise relocated artifacts, not the build environment or repository imports."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def check_cli(directory: Path, tag: str) -> None:
    executable = directory / ("opsmesh.exe" if os.name == "nt" else "opsmesh")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "UV_", "VIRTUAL_ENV", "OPSMESH_"))
    }
    environment["PATH"] = str(directory)
    environment["OPSMESH_SESSION_ID"] = secrets.token_hex(16)

    def run(*args: str) -> object:
        return json.loads(
            subprocess.check_output(
                [str(executable), *args],
                cwd=directory,
                env=environment,
                text=True,
                timeout=60,
            )
        )

    assert run("version") == {"version": tag.removeprefix("v")}
    run("doctor")
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if (
                self.headers.get("X-Session-ID") != environment["OPSMESH_SESSION_ID"]
                or self.path != "/api/v1/platform/system/version"
            ):
                self.send_error(403)
                return
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"tag": tag}).encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert run("--api-url", f"http://127.0.0.1:{server.server_port}", "status") == {"tag": tag}
        assert len(seen) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def check_server(directory: Path, tag: str, work: Path) -> None:
    # The base image contains neither Python nor uv; execute the frozen artifact directly.
    assert not (directory / "python").exists(), "Full interpreter trees must not be shipped"
    assert (directory / "_internal").is_dir()
    container = "opsmesh-native-" + secrets.token_hex(6)
    state = work / "state"
    (state / "opsmesh").mkdir(parents=True)
    plugins = state / "opsmesh/plugin-code"
    plugin = plugins / "native-check/0.1.0"
    plugin.mkdir(parents=True)
    manifest = {
        "id": "native-check",
        "version": "0.1.0",
        "name": "Native Check",
        "kind": "tool",
        "entry": "main.py",
    }
    # JSON mappings are a YAML subset; the actual manifest filename is plugin.yaml.
    (plugin / "plugin.yaml").write_text(json.dumps(manifest))
    (plugin / "helper.py").write_text("message = 'external plugin loaded'\n")
    (plugin / "main.py").write_text(
        "from .helper import message\n"
        "def echo() -> dict[str, str]:\n    return {'message': message}\n"
        "def setup(ctx):\n    ctx.tool('echo', echo)\n"
    )
    (plugins / "registry.json").write_text(
        json.dumps(
            {
                "native-check": {"manifest": manifest, "enabled": True, "config": {}},
            }
        )
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    api_url = f"http://127.0.0.1:{port}/api/v1"
    environment = {
        "OPSMESH_ENVIRONMENT": "test",
        "OPSMESH_DATABASE_URL": os.environ["OPSMESH_DATABASE_URL"],
        "OPSMESH_REDIS_URL": os.environ["OPSMESH_REDIS_URL"],
        "OPSMESH_TRACING_ENABLED": "false",
        "OPSMESH_OTEL_LOGS_ENABLED": "false",
        "OPSMESH_TEMPORAL_ADDRESS": os.environ["OPSMESH_TEMPORAL_ADDRESS"],
        "OPSMESH_TEMPORAL_NAMESPACE": os.environ.get("OPSMESH_TEMPORAL_NAMESPACE", "default"),
        "OPSMESH_STORAGE_ROOT": "/work/opsmesh/storage",
        "OPSMESH_PLUGINS_ROOT": "/work/opsmesh/plugin-code",
        "OPSMESH_API_PORT": str(port),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    base = [
        "docker",
        "run",
        "--network",
        "host",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{directory}:/bundle:ro",
        "-v",
        f"{state}:/work",
        "-w",
        "/work/opsmesh",
    ]
    for key, value in environment.items():
        base += ["-e", f"{key}={value}"]
    image = "ubuntu:22.04"
    subprocess.run(["docker", "pull", image], check=True)

    def run(*args: str) -> str:
        return subprocess.check_output(
            [*base, "--rm", image, "/bundle/opsmesh-server", *args],
            text=True,
            timeout=180,
        )

    assert json.loads(run("check"))["version"] == tag.removeprefix("v")
    run("migrate")
    run("migrate", "check")
    administrator = json.loads(run("bootstrap-admin"))

    def request(path: str, body: dict[str, object] | None, session: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if session is not None:
            headers["X-Session-ID"] = session
        payload = urllib.request.Request(
            api_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        with urllib.request.urlopen(payload, timeout=30) as response:
            return json.load(response)

    worker_container = container + "-worker"
    started: list[str] = []
    try:
        for name, command in ((worker_container, "worker"), (container, "api")):
            subprocess.run(
                [*base, "--name", name, "-d", image, "/bundle/opsmesh-server", command], check=True
            )
            started.append(name)
        for _ in range(60):
            try:
                with urllib.request.urlopen(api_url + "/health/ready", timeout=2) as r:
                    if r.status == 200:
                        running = subprocess.check_output(
                            [
                                "docker",
                                "inspect",
                                "--format",
                                "{{.State.Running}}",
                                worker_container,
                            ],
                            text=True,
                        ).strip()
                        if running != "true":
                            raise RuntimeError("Standalone Temporal Worker stopped during startup")
                        login = request(
                            "/auth/login",
                            {
                                "email": administrator["email"],
                                "password": administrator["password"],
                            },
                        )
                        output = request(
                            "/agents/plugins/native-check/tools/echo",
                            {
                                "arguments": {},
                            },
                            login["session_token"],
                        )
                        assert output["value"] == {"message": "external plugin loaded"}
                        session = login["session_token"]
                        flow = request(
                            "/workflows",
                            {
                                "name": "Native distribution acceptance",
                                "spec": {
                                    "graph": {
                                        "nodes": [
                                            {
                                                "key": "wait",
                                                "kind": "wait",
                                                "seconds": 1,
                                            }
                                        ]
                                    }
                                },
                            },
                            session,
                        )
                        revision = request(
                            f"/workflows/{flow['id']}/publish",
                            {
                                "expected_revision": flow["revision"],
                            },
                            session,
                        )
                        instance = request(
                            "/workflows/instances",
                            {
                                "revision_id": revision["id"],
                                "name": "Native distribution instance",
                                "request_key": "native-instance-" + secrets.token_hex(8),
                            },
                            session,
                        )
                        submitted = request(
                            "/workflows/runs",
                            {
                                "instance_id": instance["id"],
                                "request_key": "native-distribution-" + secrets.token_hex(8),
                                "input": {},
                            },
                            session,
                        )
                        deadline = time.monotonic() + 60
                        while time.monotonic() < deadline:
                            run_state = request(f"/workflows/runs/{submitted['id']}", None, session)
                            if run_state["status"] == "completed":
                                return
                            if run_state["status"] in {"failed", "needs_attention", "cancelled"}:
                                raise RuntimeError("Native Worker failed its durable workflow")
                            time.sleep(1)
                        raise RuntimeError("Native Worker did not complete the durable workflow")
            except urllib.error.HTTPError:
                raise
            except OSError:
                pass
            time.sleep(1)
        subprocess.run(["docker", "logs", container], check=False)
        raise RuntimeError("Standalone API/worker readiness failed")
    except BaseException:
        for name in started:
            subprocess.run(["docker", "logs", name], check=False)
        raise
    finally:
        if started:
            subprocess.run(["docker", "stop", "--timeout", "130", *started], check=False)
            subprocess.run(["docker", "rm", *started], check=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--kind", choices=["cli", "server"], required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="opsmesh-relocated-check-") as temporary:
        work = Path(temporary)
        extracted = work / "unpacked"
        if args.archive.suffix == ".zip":
            with zipfile.ZipFile(args.archive) as archive:
                archive.extractall(extracted)
        else:
            with tarfile.open(args.archive) as archive:
                archive.extractall(extracted, filter="data")
        if args.kind == "cli":
            check_cli(extracted, args.tag)
        else:
            check_server(extracted, args.tag, work)
    print(f"Relocated {args.kind} acceptance passed: {args.archive.name}", flush=True)


if __name__ == "__main__":
    main()
