"""Exercise the production Compose topology using exact candidate image digests in CI."""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from opsmesh_operator.compose import wait_for_service_completion


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
        raise RuntimeError("Candidate acceptance requires a disposable root Linux Actions runner")
    api = os.environ["API_DIGEST"]
    worker = os.environ["WORKER_DIGEST"]
    runtime = os.environ["RUNTIME_DIGEST"]
    build_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="opsmesh-candidate-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        storage = root / "data/storage"
        storage.mkdir(parents=True)
        plugins = root / "plugins"
        plugins.mkdir()
        os.chown(plugins, 10001, 10001)
        plugins.chmod(0o2750)
        os.chown(storage, 10001, 10001)
        storage.chmod(0o2770)
        values = {
            key: value or "" for key, value in dotenv_values("deploy/server/env.example").items()
        }
        values.update(
            {
                "OPSMESH_ROOT": str(root),
                "OPSMESH_BUILD_COMMIT": build_commit,
                "OPSMESH_API_IMAGE": f"ghcr.io/jhupo/opsmesh-api@{api}",
                "OPSMESH_WORKER_IMAGE": f"ghcr.io/jhupo/opsmesh-worker@{worker}",
                "OPSMESH_EXECUTION_SANDBOX_IMAGES": json.dumps(
                    [f"ghcr.io/jhupo/opsmesh-runtime@{runtime}"]
                ),
                "OPSMESH_DOCKER_GID": str(Path("/var/run/docker.sock").stat().st_gid),
                "OPSMESH_ENVIRONMENT": "production",
                "OPSMESH_ENABLE_API_DOCS": "false",
                "OPSMESH_API_RATE_LIMIT_ENABLED": "true",
                "OPSMESH_CORS_ORIGINS": '["https://candidate.example.com"]',
                "OPSMESH_TRACING_ENABLED": "false",
                "OPSMESH_OTEL_LOGS_ENABLED": "false",
                "OPSMESH_OTEL_EXPORTER_OTLP_INSECURE": "false",
                "OPSMESH_TEMPORAL_POSTGRES_PASSWORD": secrets.token_hex(32),
            }
        )
        for key in (
            "INVITATION_HASH_PEPPER",
            "CREDENTIAL_ENCRYPTION_KEY",
            "POSTGRES_PASSWORD",
        ):
            values[f"OPSMESH_{key}"] = (
                base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
                if key == "CREDENTIAL_ENCRYPTION_KEY"
                else secrets.token_hex(32)
            )
        config = root / ".env"
        config.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        config.chmod(0o600)
        compose = [
            "docker",
            "compose",
            "--project-name",
            root.name,
            "--env-file",
            str(config),
            "-f",
            str(Path("deploy/server/compose.yml").resolve()),
        ]
        subprocess.run([*compose, "config", "--quiet"], check=True)
        try:
            subprocess.run(
                [*compose, "up", "-d", "postgres", "redis", "temporal-namespace"],
                check=True,
                timeout=180,
            )
            wait_for_service_completion(compose, service="temporal-namespace", timeout=180)
            subprocess.run(
                [*compose, "up", "-d", "--no-deps", "--wait", "postgres", "redis", "temporal"],
                check=True,
                timeout=180,
            )
            subprocess.run([*compose, "run", "--rm", "--no-deps", "migrate"], check=True)
            subprocess.run(
                [
                    *compose,
                    "up",
                    "-d",
                    "--no-deps",
                    "--wait",
                    "--wait-timeout",
                    "180",
                    "api",
                    "worker",
                ],
                check=True,
            )
            subprocess.run(
                ["curl", "--fail", "http://127.0.0.1:8000/api/v1/health/ready"], check=True
            )
        finally:
            # Only this randomly named CI project's containers and volumes are removed.
            subprocess.run([*compose, "down", "--volumes"], check=True)


if __name__ == "__main__":
    main()
