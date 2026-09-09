"""Exercise the production Compose topology using exact candidate image digests in CI."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
        raise RuntimeError("Candidate acceptance requires a disposable root Linux Actions runner")
    backend = os.environ["BACKEND_DIGEST"]
    runtime = os.environ["RUNTIME_DIGEST"]
    with tempfile.TemporaryDirectory(prefix="opsmesh-candidate-") as temporary:
        root = Path(temporary)
        storage = root / "data/storage"
        storage.mkdir(parents=True)
        os.chown(storage, 10001, 10001)
        values = {key: value or "" for key, value in dotenv_values(".env.example").items()}
        values.update({
            "OPSMESH_ROOT": str(root),
            "OPSMESH_BACKEND_IMAGE": f"ghcr.io/jhupo/opsmesh@{backend}",
            "OPSMESH_RUNTIME_ALLOWED_IMAGES": json.dumps([
                f"ghcr.io/jhupo/opsmesh-runtime@{runtime}"
            ]),
            "OPSMESH_DOCKER_GID": str(Path("/var/run/docker.sock").stat().st_gid),
            "OPSMESH_ENVIRONMENT": "production",
            "OPSMESH_TRACING_ENABLED": "false",
            "OPSMESH_OTEL_LOGS_ENABLED": "false",
            "OPSMESH_READINESS_WORKER_CHECK_ENABLED": "true",
        })
        for key in (
            "INTERNAL_API_TOKEN", "WORKER_HEARTBEAT_TOKEN", "TOKEN_HASH_PEPPER",
            "CREDENTIAL_ENCRYPTION_SECRET", "PLATFORM_ADMIN_TOKEN", "POSTGRES_PASSWORD",
        ):
            values[f"OPSMESH_{key}"] = secrets.token_hex(32)
        config = root / ".env"
        config.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        config.chmod(0o600)
        compose = [
            "docker", "compose", "--project-name", root.name, "--env-file", str(config),
            "-f", str(Path("deploy/server/compose.yml").resolve()),
        ]
        try:
            subprocess.run([*compose, "up", "-d", "--wait", "postgres", "redis"], check=True)
            subprocess.run([*compose, "run", "--rm", "migrate"], check=True)
            subprocess.run([
                *compose, "up", "-d", "--wait", "--wait-timeout", "180", "api", "worker"
            ], check=True)
            subprocess.run([
                "curl", "--fail", "http://127.0.0.1:8000/api/v1/health/ready"
            ], check=True)
        finally:
            # Only this randomly named CI project's containers and volumes are removed.
            subprocess.run([*compose, "down", "--volumes"], check=True)


if __name__ == "__main__":
    main()
