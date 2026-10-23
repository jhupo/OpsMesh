"""Snapshot the managed Temporal persistence while its server is stopped."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from opsmesh_operator.commands import host_environment, run_command
from opsmesh_operator.installation import Installation

DATABASES = {"temporal": "temporal.dump", "temporal_visibility": "temporal-visibility.dump"}


class TemporalBackup:
    def __init__(self, installation: Installation) -> None:
        self.installation = installation

    def _compose(self) -> list[str]:
        from opsmesh_operator.deployments import ComposeDeployment

        return ComposeDeployment(self.installation).command(self.installation.root / "current")

    def validate(self) -> None:
        if self.installation.mode == "systemd":
            if not self.installation.temporal_service_unit:
                raise ValueError("Snapshots require a dedicated managed Temporal service unit")
            from dotenv import dotenv_values

            business = urlsplit(
                dotenv_values(self.installation.root / ".env").get("OPSMESH_DATABASE_URL") or ""
            )
            targets = [self._environment(database) for database in DATABASES]
            identities = {(item["PGHOST"], item["PGPORT"], item["PGDATABASE"]) for item in targets}
            identities.add(
                (
                    business.hostname or "127.0.0.1",
                    str(business.port or 5432),
                    unquote(business.path.lstrip("/")),
                )
            )
            if len(identities) != 3:
                raise ValueError("Business, Temporal and visibility require distinct databases")

    def size(self) -> int:
        total = 0
        for database in DATABASES:
            _, _, name = self._target(database)
            command, environment = self._command(
                database,
                "psql",
                [
                    "--no-psqlrc",
                    "-At",
                    "--dbname",
                    name,
                    "-c",
                    "SELECT pg_database_size(current_database())",
                ],
            )
            total += int(run_command(command, env=environment).strip())
        return total

    def _environment(self, database: str) -> dict[str, str]:
        from dotenv import dotenv_values

        key = (
            "OPSMESH_TEMPORAL_DATABASE_URL"
            if database == "temporal"
            else "OPSMESH_TEMPORAL_VISIBILITY_DATABASE_URL"
        )
        value = dotenv_values(self.installation.root / ".env").get(key)
        parts = urlsplit(value or "")
        if parts.scheme not in {"postgresql", "postgresql+psycopg"} or not parts.hostname:
            raise ValueError(f"Configure {key} for the dedicated Temporal persistence")
        name = unquote(parts.path.lstrip("/"))
        if not name or name in {"postgres", "template0", "template1"}:
            raise ValueError("Temporal persistence must use a dedicated database")
        return {
            **os.environ,
            "PGHOST": parts.hostname,
            "PGPORT": str(parts.port or 5432),
            "PGUSER": unquote(parts.username or ""),
            "PGPASSWORD": unquote(parts.password or ""),
            "PGDATABASE": name,
            "PGCONNECT_TIMEOUT": "10",
        }

    def _target(self, database: str) -> tuple[list[str], dict[str, str], str]:
        if self.installation.mode == "compose":
            return (
                self._compose() + ["exec", "-T", "temporal-postgres"],
                dict(os.environ),
                database,
            )
        environment = self._environment(database)
        return [], environment, environment["PGDATABASE"]

    def stop(self) -> None:
        self.validate()
        if self.installation.mode == "compose":
            run_command(self._compose() + ["stop", "temporal"])
        else:
            run_command(["systemctl", "stop", str(self.installation.temporal_service_unit)])

    def start(self) -> None:
        if self.installation.mode == "compose":
            run_command(self._compose() + ["up", "-d", "--no-deps", "--wait", "temporal"])
        else:
            self.validate()
            run_command(["systemctl", "start", str(self.installation.temporal_service_unit)])

    def _command(
        self, database: str, program: str, arguments: list[str]
    ) -> tuple[list[str], dict[str, str]]:
        prefix, environment, _ = self._target(database)
        user = ["--username", "temporal"] if prefix else []
        return prefix + [program] + user + arguments, environment

    def snapshot(self, directory: Path) -> None:
        for database, filename in DATABASES.items():
            _, _, name = self._target(database)
            command, environment = self._command(
                database, "pg_dump", ["--format=custom", "--no-owner", "--no-acl", name]
            )
            with (directory / filename).open("wb") as stream:
                result = subprocess.run(
                    command,
                    env=host_environment(environment),
                    stdout=stream,
                    stderr=subprocess.PIPE,
                    timeout=1800,
                    check=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            if result.returncode:
                raise RuntimeError("Temporal persistence backup failed")

    def restore(self, directory: Path, *, probe: bool = False) -> None:
        self.validate()
        for database, filename in DATABASES.items():
            _, _, name = self._target(database)
            target = "opsmesh_verify_" + uuid4().hex if probe else name
            command, environment = self._command(
                database, "dropdb", ["--if-exists", "--force", target]
            )
            if not probe:
                run_command(command, env=environment)
            create, _ = self._command(database, "createdb", [target])
            run_command(create, env=environment)
            try:
                restore, _ = self._command(
                    database,
                    "pg_restore",
                    ["--exit-on-error", "--no-owner", "--no-acl", "--dbname", target],
                )
                with (directory / filename).open("rb") as stream:
                    result = subprocess.run(
                        restore,
                        env=host_environment(environment),
                        stdin=stream,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=1800,
                        check=False,
                    )
                if result.returncode:
                    raise RuntimeError("Temporal persistence restoration failed")
                query, _ = self._command(
                    database,
                    "psql",
                    [
                        "--no-psqlrc",
                        "-At",
                        "--dbname",
                        target,
                        "-c",
                        "SELECT COUNT(*) FROM schema_version",
                    ],
                )
                if int(run_command(query, env=environment).strip()) < 1:
                    raise ValueError("Temporal persistence schema is missing")
            finally:
                if probe:
                    run_command(command, env=environment)
