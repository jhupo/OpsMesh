"""Exercise relocated artifacts, not the build environment or repository imports."""

from __future__ import annotations

import argparse
import json
import os
import secrets
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
    environment["OPSMESH_PLATFORM_ADMIN_TOKEN"] = secrets.token_hex(16)

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
                self.headers.get("Authorization")
                != ("Bearer " + environment["OPSMESH_PLATFORM_ADMIN_TOKEN"])
                or self.path != "/api/v1/admin/system/version"
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
    # The base image contains neither Python nor uv. Only the extracted archive supplies Python.
    container = "opsmesh-native-" + secrets.token_hex(6)
    state = work / "state"
    (state / "opsmesh").mkdir(parents=True)
    environment = {
        "OPSMESH_ENVIRONMENT": "test",
        "OPSMESH_DATABASE_URL": os.environ["OPSMESH_DATABASE_URL"],
        "OPSMESH_REDIS_URL": os.environ["OPSMESH_REDIS_URL"],
        "OPSMESH_TRACING_ENABLED": "false",
        "OPSMESH_OTEL_LOGS_ENABLED": "false",
        "OPSMESH_READINESS_WORKER_CHECK_ENABLED": "true",
        "OPSMESH_STORAGE_ROOT": "/work/opsmesh/storage",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    base = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
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
            [*base, image, "/bundle/opsmesh-server", *args],
            text=True,
            timeout=180,
        )

    assert json.loads(run("check"))["version"] == tag.removeprefix("v")
    run("migrate")
    run("migrate", "check")
    run("worker", "--once")
    # A real no-job updater tick must load its bundled SQLAlchemy/driver and persistent config.
    (state / "opsmesh/installation.json").write_text(
        json.dumps({"root": "/work/opsmesh", "mode": "systemd"})
    )
    run("updater", "--root", "/work/opsmesh", "--once")
    subprocess.run(
        [*base, "--name", container, "-d", image, "/bundle/opsmesh-server", "api"], check=True
    )
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8000/api/v1/health/ready", timeout=2
                ) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        subprocess.run(["docker", "logs", container], check=False)
        raise RuntimeError("Standalone API/worker readiness failed")
    finally:
        subprocess.run(["docker", "stop", "--time", "10", container], check=True)


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
