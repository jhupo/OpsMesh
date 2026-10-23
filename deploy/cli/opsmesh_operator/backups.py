from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from opsmesh_operator.commands import host_environment, run_command
from opsmesh_operator.files import atomic_write, sync_directory
from opsmesh_operator.installation import Installation
from opsmesh_operator.temporal_backup import DATABASES, TemporalBackup


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


def database_revision(installation: Installation) -> str:
    import psycopg

    env = postgres_env(installation)
    with psycopg.connect(
        host=env["PGHOST"],
        port=env["PGPORT"],
        user=env["PGUSER"],
        password=env["PGPASSWORD"],
        dbname=env["PGDATABASE"],
    ) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    if row is None:
        raise RuntimeError("The database migration revision is unknown")
    return str(row[0])


def require_quiescent_execution(installation: Installation) -> None:
    """After stopping API/workers, refuse snapshots while external effects can still occur."""
    import psycopg

    env = postgres_env(installation)
    with psycopg.connect(
        host=env["PGHOST"],
        port=env["PGPORT"],
        user=env["PGUSER"],
        password=env["PGPASSWORD"],
        dbname=env["PGDATABASE"],
    ) as connection:
        row = connection.execute("""
            SELECT EXISTS (SELECT 1 FROM execution_runs
                           WHERE status NOT IN ('completed', 'failed', 'cancelled'))
                OR EXISTS (SELECT 1 FROM execution_operations
                           WHERE status IN ('started', 'uncertain'))
                OR EXISTS (SELECT 1 FROM workflow_runs
                           WHERE status NOT IN ('completed', 'failed', 'cancelled'))
                OR EXISTS (SELECT 1 FROM workflow_operations
                           WHERE status IN ('started', 'uncertain'))
                OR EXISTS (SELECT 1 FROM sandbox_operations
                           WHERE status NOT IN ('completed', 'rejected'))
                OR EXISTS (SELECT 1 FROM sandbox_environments WHERE status <> 'closed')
        """).fetchone()
    if row is None or row[0]:
        raise RuntimeError(
            "Snapshot refused: finish or reconcile runs and operations "
            "and close sandbox environments "
            "before retrying host maintenance"
        )


class BackupStore:
    def __init__(self, installation: Installation) -> None:
        self.installation = installation

    def path(self, backup_id: str) -> Path:
        return self.installation.root / "backups" / str(UUID(backup_id))

    def create(self, *, database_revision: str) -> str:
        """Caller must hold maintenance and stop API/workers for a coordinated snapshot."""
        temporal = TemporalBackup(self.installation)
        temporal.validate()
        require_quiescent_execution(self.installation)
        self._check_capacity()
        backup_id = str(uuid4())
        directory = self.path(backup_id)
        directory.mkdir(parents=True, mode=0o700)
        dump = directory / "database.dump"
        temporal.stop()
        try:
            temporal.snapshot(directory)
            self._dump_database(dump)
        finally:
            temporal.start()
        archive_path = directory / "storage.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            storage = self.installation.root / "data/storage"
            for path in sorted(storage.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Storage backup refuses symlinks")
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(storage).as_posix())
        with archive_path.open("rb") as stream:
            os.fsync(stream.fileno())
        plugins_path = directory / "plugins.tar.gz"
        with tarfile.open(plugins_path, "w:gz") as archive:
            plugins = self.installation.root / "plugins"
            for path in sorted(plugins.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Plugin backup refuses symlinks")
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(plugins).as_posix())
        with plugins_path.open("rb") as stream:
            os.fsync(stream.fileno())
        config = directory / "configuration.env"
        atomic_write(config, (self.installation.root / ".env").read_text("utf-8"))
        metadata = {
            "format": 3,
            "id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "database_revision": database_revision,
            "release": self.installation.current().model_dump(mode="json"),
            "files": {
                path.name: digest(path)
                for path in (
                    dump,
                    archive_path,
                    plugins_path,
                    config,
                    *(directory / n for n in DATABASES.values()),
                )
            },
        }
        atomic_write(directory / "backup.json", json.dumps(metadata, indent=2))
        self.verify(backup_id, restore_check=True)
        sync_directory(directory.parent)
        return backup_id

    def _dump_database(self, dump: Path) -> None:
        with dump.open("wb") as stream:
            result = subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", "--no-acl"],
                env=host_environment(postgres_env(self.installation)),
                stdout=stream,
                stderr=subprocess.PIPE,
                timeout=1800,
                check=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if result.returncode:
            raise RuntimeError("Database backup failed; incomplete backup retained for inspection")

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
            for base in ("data/storage", "plugins")
            for path in (self.installation.root / base).rglob("*")
            if path.is_file()
        )
        required = (int(row[0]) + TemporalBackup(self.installation).size()) * 3
        required += storage_size * 2 + 512_000_000
        if shutil.disk_usage(self.installation.root).free < required:
            raise ValueError("Insufficient space for snapshot and restore verification")

    def verify(self, backup_id: str, *, restore_check: bool = False) -> dict[str, object]:
        directory = self.path(backup_id)
        metadata: dict[str, object] = json.loads((directory / "backup.json").read_text("utf-8"))
        files = metadata.get("files")
        if (
            metadata.get("format") != 3
            or not isinstance(files, dict)
            or set(files)
            != {
                "database.dump",
                "storage.tar.gz",
                "plugins.tar.gz",
                "configuration.env",
                *DATABASES.values(),
            }
        ):
            raise ValueError("Invalid backup inventory")
        for name, expected in files.items():
            if digest(directory / name) != expected:
                raise ValueError("Backup checksum mismatch")
        run_command(["pg_restore", "--list", str(directory / "database.dump")])
        for name in ("storage.tar.gz", "plugins.tar.gz"):
            with tarfile.open(directory / name) as archive:
                members = archive.getmembers()
            for member in members:
                if (
                    not member.isfile()
                    or PurePosixPath(member.name).is_absolute()
                    or ".." in PurePosixPath(member.name).parts
                    or "\\" in member.name
                    or ":" in member.name
                ):
                    raise ValueError("Unsafe backup archive")
        if restore_check:
            self._restore_probe(directory, expected_revision=str(metadata["database_revision"]))
            TemporalBackup(self.installation).restore(directory, probe=True)
            atomic_write(directory / "verified", datetime.now(UTC).isoformat())
        return metadata

    def _restore_probe(self, directory: Path, *, expected_revision: str) -> None:
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
                with psycopg.connect(
                    host=env["PGHOST"],
                    port=env["PGPORT"],
                    user=env["PGUSER"],
                    password=env["PGPASSWORD"],
                    dbname=database,
                ) as restored:
                    row = restored.execute("SELECT version_num FROM alembic_version").fetchone()
                    if row is None or str(row[0]) != expected_revision:
                        raise ValueError("Restored schema differs from the backup revision")
            finally:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )

    def restore(self, backup_id: str, *, acknowledge_data_loss: bool) -> None:
        """Called only by explicit host recovery with API/workers and external processes stopped."""
        if not acknowledge_data_loss:
            raise ValueError("Database restoration requires explicit data-loss acknowledgement")
        directory = self.path(backup_id)
        self._validate_restore_connections(directory)
        self.verify(backup_id, restore_check=True)
        require_quiescent_execution(self.installation)
        temporal = TemporalBackup(self.installation)
        temporal.stop()
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
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
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
        temporal.restore(directory)
        storage = root / "data/storage"
        # Preserve the post-backup filesystem for manual salvage; never recursively delete it.
        storage.rename(root / "data" / ("before-restore-" + uuid4().hex))
        staging.rename(storage)
        os.chown(storage, 10001, 10001)
        storage.chmod(0o2770)
        for path in storage.rglob("*"):
            os.chown(path, 10001, 10001)
            path.chmod(0o2770 if path.is_dir() else 0o660)
        plugin_staging = root / ("plugins-restore-" + uuid4().hex)
        plugin_staging.mkdir(mode=0o755)
        with tarfile.open(directory / "plugins.tar.gz") as archive:
            archive.extractall(plugin_staging, filter="data")
        os.chown(plugin_staging, 10001, 10001)
        plugin_staging.chmod(0o2750)
        for path in plugin_staging.rglob("*"):
            os.chown(path, 10001, 10001)
            path.chmod(
                0o2750 if path.is_dir() else 0o660 if path.name == ".registry.lock" else 0o640
            )
        plugins = root / "plugins"
        if plugins.exists():
            plugins.rename(root / ("plugins-before-restore-" + uuid4().hex))
        plugin_staging.rename(plugins)
        config = root / ".env"
        identity = config.stat()
        atomic_write(
            config,
            (directory / "configuration.env").read_text("utf-8"),
            mode=stat.S_IMODE(identity.st_mode),
        )
        os.chown(config, identity.st_uid, identity.st_gid)

    def _validate_restore_connections(self, directory: Path) -> None:
        from dotenv import dotenv_values

        current = dotenv_values(self.installation.root / ".env")
        saved = dotenv_values(directory / "configuration.env")
        for key in (
            "OPSMESH_DATABASE_URL",
            "OPSMESH_POSTGRES_PASSWORD",
            "OPSMESH_TEMPORAL_ADDRESS",
            "OPSMESH_TEMPORAL_NAMESPACE",
            "OPSMESH_TEMPORAL_DATABASE_URL",
            "OPSMESH_TEMPORAL_VISIBILITY_DATABASE_URL",
            "OPSMESH_TEMPORAL_POSTGRES_PASSWORD",
            "OPSMESH_REDIS_URL",
        ):
            if current.get(key) != saved.get(key):
                raise ValueError(
                    f"Restore connection configuration differs for {key}; "
                    "reconcile the installation and snapshot before recovery"
                )
