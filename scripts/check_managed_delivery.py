"""Exercise fresh installation, a workflow instance and snapshot restore on Linux."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import time
from pathlib import Path

import httpx
from opsmesh_operator.backups import BackupStore
from opsmesh_operator.cli import build_argument_parser, execute
from opsmesh_operator.deployments import deployment_for
from opsmesh_operator.files import atomic_write
from opsmesh_operator.installation import Installation
from opsmesh_operator.installer import install


def prepare_systemd(root: Path) -> None:
    password = os.environ["ACCEPTANCE_POSTGRES_PASSWORD"]
    temporal_password = os.environ["ACCEPTANCE_TEMPORAL_POSTGRES_PASSWORD"]
    values = {
        "OPSMESH_ROOT": str(root),
        "OPSMESH_ENVIRONMENT": "production",
        "OPSMESH_DATABASE_URL": f"postgresql+psycopg://opsmesh:{password}@127.0.0.1:5432/opsmesh",
        "OPSMESH_REDIS_URL": "redis://127.0.0.1:6379/0",
        "OPSMESH_TEMPORAL_ADDRESS": "127.0.0.1:7233",
        "OPSMESH_TEMPORAL_DATABASE_URL": f"postgresql://temporal:{temporal_password}@127.0.0.1:5433/temporal",
        "OPSMESH_TEMPORAL_VISIBILITY_DATABASE_URL": f"postgresql://temporal:{temporal_password}@127.0.0.1:5433/temporal_visibility",
        "OPSMESH_STORAGE_ROOT": str(root / "data/storage"),
        "OPSMESH_PLUGINS_ROOT": str(root / "plugins"),
        "OPSMESH_TEMPORAL_WORKER_IDENTITY": "managed-acceptance",
        "OPSMESH_INVITATION_HASH_PEPPER": secrets.token_hex(32),
        "OPSMESH_CREDENTIAL_ENCRYPTION_KEY": base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode(),
        "OPSMESH_TRACING_ENABLED": "false",
        "OPSMESH_OTEL_LOGS_ENABLED": "false",
        "OPSMESH_API_RATE_LIMIT_ENABLED": "true",
        "OPSMESH_METRICS_HOST": "127.0.0.1",
        "OPSMESH_METRICS_PORT": "9000",
        "OPSMESH_ENABLE_API_DOCS": "false",
        "OPSMESH_CORS_ORIGINS": '["https://acceptance.invalid"]',
    }
    atomic_write(root / ".env", "".join(f"{key}={value}\n" for key, value in values.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--mode", choices=["compose", "systemd"], required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0:
        raise ValueError("Managed acceptance requires a disposable GitHub Actions Linux root host")
    root = Path("/opt/opsmesh-managed-acceptance")
    if root.exists() or Path("/opt/opsmesh/installation.json").exists():
        raise ValueError("Refusing to overwrite an installation")
    root.mkdir(mode=0o750)
    if args.mode == "systemd":
        prepare_systemd(root)
    installation = Installation(
        root=root,
        mode=args.mode,
        temporal_service_unit=(
            "opsmesh-acceptance-temporal.service" if args.mode == "systemd" else None
        ),
    )
    credentials = install(installation, args.tag, "https://acceptance.invalid")
    from opsmesh_operator.commands import run_command

    executable = root / "operator/opsmesh"
    assert json.loads(run_command([str(executable), "version"]))["version"] == args.tag[1:]
    discovered = json.loads(
        run_command(
            [
                str(executable),
                "--root",
                str(root),
                "update",
                "--check",
                "--version",
                args.tag,
            ]
        )
    )
    assert discovered["update_available"] is False
    manifest = installation.current()
    if not manifest.database_revision:
        raise AssertionError("The release must declare its current migration head")
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30, trust_env=False) as api:
        login = api.post(
            "/api/v1/auth/login",
            json={"email": credentials["email"], "password": credentials["password"]},
        )
        login.raise_for_status()
        os.environ["OPSMESH_SESSION_ID"] = login.json()["session_token"]
        api.headers["X-Session-ID"] = os.environ["OPSMESH_SESSION_ID"]

        def request(method: str, path: str, body: dict[str, object] | None = None) -> dict:
            response = api.request(method, "/api/v1" + path, json=body)
            response.raise_for_status()
            return response.json()

        definition = request(
            "POST",
            "/workflows",
            {
                "name": "Snapshot evidence",
                "spec": {"graph": {"nodes": [{"key": "receipt", "kind": "wait", "seconds": 1}]}},
            },
        )
        revision = request(
            "POST",
            f"/workflows/{definition['id']}/publish",
            {"expected_revision": definition["revision"]},
        )
        instance = request(
            "POST",
            "/workflows/instances",
            {
                "revision_id": revision["id"],
                "name": "Snapshot instance",
                "request_key": "snapshot-instance",
            },
        )
        run_id = request(
            "POST",
            "/workflows/runs",
            {
                "instance_id": instance["id"],
                "request_key": "snapshot-flow",
                "input": {},
            },
        )["id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            flow = request("GET", f"/workflows/runs/{run_id}")
            if flow["status"] == "completed":
                break
            if flow["status"] in {"failed", "needs_attention", "cancelled"}:
                raise AssertionError("The installed Worker failed the real flow")
            time.sleep(1)
        else:
            raise TimeoutError("The installed Worker did not complete the flow")
        marker = root / "data/storage/acceptance-marker"
        marker.write_text("before", encoding="utf-8")
        arguments = build_argument_parser()

        def command(*parts: str) -> object:
            return execute(arguments.parse_args(["--root", str(root), *parts]))

        backup = command("backup", "create")
        backup_id = str(backup["id"])
        checked = BackupStore(installation).verify(backup_id, restore_check=True)
        assert checked["database_revision"] == manifest.database_revision
        changed = request(
            "PUT",
            f"/workflows/{definition['id']}/draft",
            {
                "name": "Changed after snapshot",
                "expected_revision": definition["revision"] + 1,
                "spec": {"graph": {"nodes": [{"key": "receipt", "kind": "wait", "seconds": 1}]}},
            },
        )
        assert changed["name"] == "Changed after snapshot"
        marker.write_text("after", encoding="utf-8")
        command("backup", "restore", backup_id, "--acknowledge-data-loss")
        assert request("GET", f"/workflows/{definition['id']}")["name"] == "Snapshot evidence"
        assert marker.read_text(encoding="utf-8") == "before"
        assert request("GET", f"/workflows/runs/{run_id}")["status"] == "completed"
        deployment = deployment_for(installation)
        deployment.stop()
        deployment.start(manifest)
        deployment.healthy()
        assert request("GET", f"/workflows/runs/{run_id}")["status"] == "completed"
        assert command("status")["version"] == args.tag.removeprefix("v")
        # Verify a corrupted snapshot is denied before restoring any database.
        dump = BackupStore(installation).path(backup_id) / "database.dump"
        with dump.open("ab") as stream:
            stream.write(b"tampered")
        try:
            BackupStore(installation).verify(backup_id)
        except ValueError:
            pass
        else:
            raise AssertionError("Corrupted snapshot was accepted")
    print(
        json.dumps(
            {
                "mode": args.mode,
                "schema": manifest.database_revision,
                "installed": True,
                "flow": "completed",
                "cohort_restored": True,
                "tamper_denied": True,
            }
        )
    )


if __name__ == "__main__":
    main()
