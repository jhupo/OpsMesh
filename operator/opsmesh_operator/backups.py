from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from opsmesh_operator.commands import run_command
from opsmesh_operator.files import atomic_write
from opsmesh_operator.installation import Installation


def postgres_env(installation: Installation) -> dict[str, str]:
    from dotenv import dotenv_values

    values = dotenv_values(installation.root / ".env")
    url = urlsplit(
        str(values["OPSMESH_DATABASE_URL"]).replace("postgresql+psycopg:", "postgresql:")
    )
    return {
        **os.environ,
        "PGHOST": url.hostname or "127.0.0.1",
        "PGPORT": str(url.port or 5432),
        "PGUSER": unquote(url.username or ""),
        "PGPASSWORD": unquote(url.password or ""),
        "PGDATABASE": url.path.lstrip("/"),
        "PGCONNECT_TIMEOUT": "10",
    }


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class BackupStore:
    def __init__(self, installation: Installation) -> None:
        self.installation = installation

    def path(self, backup_id: str) -> Path:
        return self.installation.root / "backups" / str(UUID(backup_id))

    def create(self, *, database_revision: str) -> str:
        """Caller must hold maintenance and stop API/workers for a coordinated snapshot."""
        self._check_capacity()
        backup_id = str(uuid4())
        directory = self.path(backup_id)
        directory.mkdir(parents=True, mode=0o700)
        dump = directory / "database.dump"
        with dump.open("wb") as stream:
            result = subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", "--no-acl"],
                env=postgres_env(self.installation),
                stdout=stream,
                stderr=subprocess.PIPE,
                timeout=1800,
                check=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if result.returncode:
            raise RuntimeError("Database backup failed; incomplete backup retained for inspection")
        archive_path = directory / "storage.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            storage = self.installation.root / "data/storage"
            for path in sorted(storage.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Storage backup refuses symlinks")
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(storage).as_posix())
        config = directory / "configuration.env"
        atomic_write(config, (self.installation.root / ".env").read_text("utf-8"))
        metadata = {
            "id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "database_revision": database_revision,
            "release": self.installation.current().model_dump(mode="json"),
            "files": {path.name: digest(path) for path in (dump, archive_path, config)},
        }
        atomic_write(directory / "backup.json", json.dumps(metadata, indent=2))
        self.verify(backup_id, restore_check=True)
        return backup_id

    def _check_capacity(self) -> None:
        import psycopg

        env = postgres_env(self.installation)
        with psycopg.connect(
            host=env["PGHOST"],
            port=env["PGPORT"],
            user=env["PGUSER"],
            password=env["PGPASSWORD"],
            dbname=env["PGDATABASE"],
        ) as connection:
            row = connection.execute("SELECT pg_database_size(current_database())").fetchone()
        if row is None:
            raise RuntimeError("Cannot determine database backup capacity")
        storage_size = sum(
            path.stat().st_size
            for path in (self.installation.root / "data/storage").rglob("*")
            if path.is_file()
        )
        required = int(row[0]) * 3 + storage_size * 2 + 512_000_000
        if shutil.disk_usage(self.installation.root).free < required:
            raise ValueError("Insufficient space for snapshot and restore verification")

    def verify(self, backup_id: str, *, restore_check: bool = False) -> dict[str, object]:
        directory = self.path(backup_id)
        metadata: dict[str, object] = json.loads((directory / "backup.json").read_text("utf-8"))
        files = metadata.get("files")
        if not isinstance(files, dict) or set(files) != {
            "database.dump",
            "storage.tar.gz",
            "configuration.env",
        }:
            raise ValueError("Invalid backup inventory")
        for name, expected in files.items():
            if digest(directory / name) != expected:
                raise ValueError("Backup checksum mismatch")
        run_command(["pg_restore", "--list", str(directory / "database.dump")])
        with tarfile.open(directory / "storage.tar.gz") as archive:
            for member in archive.getmembers():
                if (
                    not member.isfile()
                    or PurePosixPath(member.name).is_absolute()
                    or ".." in PurePosixPath(member.name).parts
                    or "\\" in member.name
                    or ":" in member.name
                ):
                    raise ValueError("Unsafe backup archive")
        if restore_check:
            self._restore_probe(directory)
            atomic_write(directory / "verified", datetime.now(UTC).isoformat())
        return metadata

    def _restore_probe(self, directory: Path) -> None:
        import psycopg
        from psycopg import sql

        env = postgres_env(self.installation)
        database = "opsmesh_verify_" + uuid4().hex
        with psycopg.connect(
            host=env["PGHOST"],
            port=env["PGPORT"],
            user=env["PGUSER"],
            password=env["PGPASSWORD"],
            dbname="postgres",
            autocommit=True,
        ) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            try:
                run_command(
                    [
                        "pg_restore",
                        "--exit-on-error",
                        "--no-owner",
                        "--no-acl",
                        "--dbname",
                        database,
                        str(directory / "database.dump"),
                    ],
                    env=env,
                    timeout=1800,
                )
            finally:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )

    def restore(self, backup_id: str, *, acknowledge_data_loss: bool) -> None:
        """Called only by explicit host recovery, while admission is closed and services stopped."""
        if not acknowledge_data_loss:
            raise ValueError("Database restoration requires explicit data-loss acknowledgement")
        self.verify(backup_id, restore_check=True)
        directory = self.path(backup_id)
        root = self.installation.root
        staging = root / "data" / ("restore-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        with tarfile.open(directory / "storage.tar.gz") as archive:
            archive.extractall(staging, filter="data")
        import psycopg
        from psycopg import sql

        env = postgres_env(self.installation)
        database = env["PGDATABASE"]
        if database in {"postgres", "template0", "template1"}:
            raise ValueError("Refusing restoration into a PostgreSQL system database")
        with psycopg.connect(
            host=env["PGHOST"],
            port=env["PGPORT"],
            user=env["PGUSER"],
            password=env["PGPASSWORD"],
            dbname="postgres",
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        run_command(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--dbname",
                database,
                str(directory / "database.dump"),
            ],
            env=env,
            timeout=1800,
        )
        storage = root / "data/storage"
        # Preserve the post-backup filesystem for manual salvage; never recursively delete it.
        storage.rename(root / "data" / ("before-restore-" + uuid4().hex))
        staging.rename(storage)
        os.chown(storage, 10001, 10001)
        for path in storage.rglob("*"):
            os.chown(path, 10001, 10001)
        atomic_write(root / ".env", (directory / "configuration.env").read_text("utf-8"))
