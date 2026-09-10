"""Destructive acceptance confined to a fresh, disposable GitHub-hosted Linux VM."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import tarfile
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

import httpx
import psycopg
from dotenv import dotenv_values
from opsmesh_operator.backups import postgres_env
from opsmesh_operator.commands import run_command
from opsmesh_operator.files import atomic_write
from opsmesh_operator.installation import Installation
from opsmesh_operator.releases import ReleaseSource

ROOT = Path("/opt/opsmesh-acceptance")


def wait_for(predicate: Callable[[], bool], description: str, timeout: int = 900) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError(f"Acceptance timed out: {description}")


class Unavailable(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(503)
        self.end_headers()

    def log_message(self, *_: object) -> None:
        pass


class Acceptance:
    def __init__(self, mode: str, tag: str, previous: str) -> None:
        self.mode, self.tag, self.previous = mode, tag, previous
        self.installation = Installation(root=ROOT, mode=mode)
        self.cli = ROOT / "client/opsmesh"
        self.environment = dict(os.environ)
        self.database: dict[str, str] = {}

    def command(self, *args: str) -> str:
        return run_command(
            [str(self.cli), "--root", str(ROOT), *args], env=self.environment, timeout=1800
        )

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(**self.database, autocommit=True)

    def install(self) -> None:
        if ROOT.exists():
            raise ValueError("Acceptance refuses to reuse an installation directory")
        ROOT.mkdir(mode=0o750)
        # A temporary workflow token is kept only in root-owned updater environment, never logs.
        atomic_write(ROOT / "updater.env", f"GH_TOKEN={os.environ['GH_TOKEN']}\n")
        source = ReleaseSource("jhupo/OpsMesh")
        directory = ROOT / "client-download"
        manifest = source.fetch_manifest(self.tag, directory)
        record = next(
            file
            for file in manifest.files
            if file.name == f"opsmesh-cli-{self.tag}-linux-amd64.tar.gz"
        )
        archive = source.download_file(manifest, record, directory)
        source.verify(archive, self.tag, commit=manifest.commit)
        with tarfile.open(archive) as package:
            package.extractall(ROOT / "client", filter="data")
        if self.mode == "systemd":
            values = {
                "OPSMESH_ROOT": str(ROOT),
                "OPSMESH_ENVIRONMENT": "production",
                "OPSMESH_DATABASE_URL": "postgresql+psycopg://opsmesh:opsmesh@127.0.0.1:5432/opsmesh",
                "OPSMESH_REDIS_URL": "redis://127.0.0.1:6379/0",
                "OPSMESH_STORAGE_ROOT": str(ROOT / "data/storage"),
                "OPSMESH_RELEASE_UPDATE_ENABLED": "true",
                "OPSMESH_READINESS_WORKER_CHECK_ENABLED": "true",
                "OPSMESH_TRACING_ENABLED": "false",
                "OPSMESH_OTEL_LOGS_ENABLED": "false",
                "OPSMESH_ENABLE_API_DOCS": "false",
                "OPSMESH_API_RATE_LIMIT_ENABLED": "true",
                "OPSMESH_CORS_ORIGINS": '["https://acceptance.example.org"]',
            }
            for name in (
                "INTERNAL_API_TOKEN",
                "PLATFORM_ADMIN_TOKEN",
                "TOKEN_HASH_PEPPER",
                "CREDENTIAL_ENCRYPTION_SECRET",
                "WORKER_HEARTBEAT_TOKEN",
            ):
                values[f"OPSMESH_{name}"] = secrets.token_hex(32)
            atomic_write(
                ROOT / ".env", "".join(f"{key}={value}\n" for key, value in values.items())
            )
        self.command(
            "install",
            "--version",
            self.tag,
            "--mode",
            self.mode,
            "--origin",
            "https://acceptance.example.org",
        )
        settings = dotenv_values(ROOT / ".env")
        self.environment["OPSMESH_PLATFORM_ADMIN_TOKEN"] = str(
            settings["OPSMESH_PLATFORM_ADMIN_TOKEN"]
        )
        env = postgres_env(self.installation)
        self.database = {
            "host": env["PGHOST"],
            "port": env["PGPORT"],
            "user": env["PGUSER"],
            "password": env["PGPASSWORD"],
            "dbname": env["PGDATABASE"],
        }
        # Prevent automatic restart from racing fault inspection; recovery explicitly restarts it.
        atomic_write(
            Path("/etc/systemd/system/opsmesh-updater.service.d/acceptance.conf"),
            "[Service]\nRestart=no\n",
            mode=0o644,
        )
        run_command(["systemctl", "daemon-reload"])
        with self.connect() as connection:
            connection.execute("CREATE TABLE delivery_acceptance_canary (value text NOT NULL)")
            connection.execute("INSERT INTO delivery_acceptance_canary VALUES ('before')")
        artifact = ROOT / "data/storage/acceptance/canary.txt"
        artifact.parent.mkdir(mode=0o2770)
        artifact.write_text("before", encoding="utf-8")
        os.chown(artifact.parent, 10001, 10001)
        os.chown(artifact, 10001, 10001)
        artifact.chmod(0o660)
        self.assert_version(self.tag)
        print(f"PASS {self.mode}: signed native CLI managed installation", flush=True)

    def status(self, job_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM platform_update_jobs WHERE id=%s", (job_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Missing acceptance job")
        return str(row[0])

    def plan(self, tag: str, action: str) -> str:
        result = json.loads(
            self.command("update", action, "--version", tag, "--idempotency-key", uuid4().hex)
        )
        job_id = str(result["id"])
        wait_for(lambda: self.status(job_id) in {"ready", "failed"}, "validated plan")
        result = json.loads(self.command("update", "status", job_id))
        if result["status"] != "ready":
            raise RuntimeError(f"Plan failed: {result['error_code']}")
        self.command("update", "apply", "--plan", job_id, "--fingerprint", result["plan_sha256"])
        return job_id

    def journal(self, job_id: str) -> dict:
        path = ROOT / "updates" / f"{job_id}.json"
        return json.loads(path.read_bytes()) if path.exists() else {}

    def assert_version(self, tag: str) -> None:
        result = json.loads(self.command("status"))
        if result["tag"] != tag or self.installation.current().tag != tag:
            raise AssertionError("HTTP version and deployed release must both match target")
        with httpx.Client(timeout=10, trust_env=False) as client:
            client.get(self.installation.health_url).raise_for_status()
        if json.loads((ROOT / "updater/BUILD.json").read_bytes())["tag"] != self.tag:
            raise AssertionError("Application update replaced independent updater")

    def normal(self, tag: str, action: str) -> None:
        job_id = self.plan(tag, action)
        wait_for(
            lambda: self.status(job_id) in {"succeeded", "failed", "recovery_required"},
            "normal managed update",
        )
        if self.status(job_id) != "succeeded":
            raise AssertionError(
                f"Managed update failed: {self.status(job_id)}, {self.journal(job_id).get('phase')}"
            )
        self.assert_version(tag)
        print(f"PASS {self.mode}: approved {action} to {tag}", flush=True)

    def interrupted(self, strategy: str) -> None:
        job_id = self.plan(self.tag, "plan")
        wait_for(lambda: self.journal(job_id).get("phase") == "backup", "backup entry")
        # The real DB row lock holds the next checkpoint after its fsynced journal write.
        # Killing the actual systemd process now deterministically tests crash recovery.
        with self.connect() as connection, connection.transaction():
            connection.execute(
                "SELECT id FROM platform_update_jobs WHERE id=%s FOR UPDATE", (job_id,)
            )
            wait_for(lambda: self.journal(job_id).get("phase") == "backed_up", "durable backup")
            run_command(
                ["systemctl", "kill", "--signal=SIGKILL", "--kill-whom=all", "opsmesh-updater"]
            )
        wait_for(
            lambda: (
                subprocess.run(
                    ["systemctl", "is-active", "--quiet", "opsmesh-updater"], check=False
                ).returncode
                != 0
            ),
            "killed updater",
            30,
        )
        run_command(["systemctl", "reset-failed", "opsmesh-updater"])
        run_command(["systemctl", "start", "opsmesh-updater"])
        wait_for(
            lambda: self.status(job_id) == "recovery_required", "interruption classification", 60
        )
        run_command(["systemctl", "stop", "opsmesh-updater"])
        if strategy == "restore":
            with self.connect() as connection:
                connection.execute("UPDATE delivery_acceptance_canary SET value='after'")
            (ROOT / "data/storage/acceptance/canary.txt").write_text("after", encoding="utf-8")
            denied = subprocess.run(
                [
                    str(self.cli),
                    "--root",
                    str(ROOT),
                    "update",
                    "recover",
                    "--plan",
                    job_id,
                    "--strategy",
                    "restore",
                ],
                capture_output=True,
                env=self.environment,
                check=False,
            )
            if denied.returncode == 0:
                raise AssertionError("Restore without data-loss acknowledgement was accepted")
        arguments = ["update", "recover", "--plan", job_id, "--strategy", strategy]
        if strategy == "restore":
            arguments.append("--ack-data-loss")
        self.command(*arguments)
        run_command(["systemctl", "start", "opsmesh-updater"])
        self.assert_version(self.tag if strategy == "resume" else self.previous)
        with self.connect() as connection:
            if connection.execute("SELECT maintenance FROM platform_installation").fetchone() != (
                False,
            ):
                raise AssertionError("Recovery left admission closed")
            if strategy == "restore" and connection.execute(
                "SELECT value FROM delivery_acceptance_canary"
            ).fetchone() != ("before",):
                raise AssertionError("Database canary was not restored")
        if strategy == "restore":
            if (ROOT / "data/storage/acceptance/canary.txt").read_text() != "before":
                raise AssertionError("Filesystem canary was not restored")
            if not any(
                path.read_text() == "after"
                for path in (ROOT / "data").glob("before-restore-*/acceptance/canary.txt")
            ):
                raise AssertionError("Post-backup files were not preserved for salvage")
        print(
            f"PASS {self.mode}: killed updater, durable classification, explicit {strategy}",
            flush=True,
        )

    def failed_start(self) -> None:
        job_id = self.plan(self.tag, "plan")
        wait_for(lambda: self.journal(job_id).get("phase") == "backup", "stopped application")
        server = ThreadingHTTPServer(("127.0.0.1", 8000), Unavailable)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            wait_for(lambda: self.status(job_id) == "recovery_required", "failed startup")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        run_command(["systemctl", "stop", "opsmesh-updater"])
        self.command("update", "recover", "--plan", job_id, "--strategy", "rollback")
        run_command(["systemctl", "start", "opsmesh-updater"])
        self.assert_version(self.previous)
        print(
            f"PASS {self.mode}: real port-bind/startup failure and application rollback", flush=True
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["compose", "systemd"], required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--previous", required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0 or os.name != "posix":
        raise ValueError("Run only as root on a fresh disposable GitHub Linux runner")
    acceptance = Acceptance(args.mode, args.tag, args.previous)
    acceptance.install()
    acceptance.normal(args.previous, "rollback")
    acceptance.normal(args.tag, "plan")
    acceptance.normal(args.previous, "rollback")
    acceptance.interrupted("resume")
    acceptance.normal(args.previous, "rollback")
    acceptance.failed_start()
    acceptance.interrupted("restore")
    acceptance.normal(args.tag, "plan")
    print(f"PASS {args.mode}: complete managed delivery acceptance", flush=True)


if __name__ == "__main__":
    main()
