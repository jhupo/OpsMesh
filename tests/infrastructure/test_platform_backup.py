import asyncio
from pathlib import Path

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.entrypoints.cli.main import _parser
from opsmesh.framework.errors import OpsMeshError
from opsmesh.maintenance import database as database_backup
from opsmesh.maintenance.backup import create_backup, restore_backup, verify_backup
from opsmesh.modules.workspaces.api import WorkspaceManifest


async def seed_platform(root: Path):
    settings = OpsMeshSettings(data_directory=root, worker_enabled=False)
    application = OpsMeshApplication(settings)
    await application.initialize(admin_username="admin", admin_password="local-test-password")
    try:
        user = (await application.services.accounts.list_users())[0]
        await application.create_workspace(user.id, "project")
        await application.services.workspaces.write(
            "project", "nested/report.txt", b"retained", read_only=True
        )
        await application.services.workspaces.materialize(
            "project", WorkspaceManifest(directories=("empty",))
        )
        snapshot = await application.services.workspaces.snapshot("project")
        await application.services.workspaces.put_blob(b"shared-memory-or-checkpoint")
        return settings, user.id, snapshot.id
    finally:
        await application.close()


async def test_sqlite_platform_backup_restores_accounts_workspaces_snapshots_and_blobs(tmp_path):
    settings, principal_id, snapshot_id = await seed_platform(tmp_path / "original")
    bundle = tmp_path / "backup"
    manifest = await create_backup(settings, bundle, maintenance=True)
    assert (
        await verify_backup(bundle, expected_manifest_digest=manifest.manifest_digest) == manifest
    )
    target = OpsMeshSettings(data_directory=tmp_path / "restored", worker_enabled=False)
    await restore_backup(
        target, bundle, expected_manifest_digest=manifest.manifest_digest, maintenance=True
    )
    assert not (target.data_directory / ".restore-incomplete").exists()
    restored = OpsMeshApplication(target)
    try:
        await restored.services.database.verify_schema()
        await restored.authorize_workspace(principal_id, "project")
        assert (
            await restored.services.workspaces.read("project", "nested/report.txt") == b"retained"
        )
        assert (await restored.services.workspaces.root_path("project") / "empty").is_dir()
        with pytest.raises(OpsMeshError) as read_only:
            await restored.services.workspaces.write("project", "nested/report.txt", b"changed")
        assert read_only.value.code == "AUTHORIZATION_DENIED"
        await restored.services.workspaces.restore(snapshot_id, "from-snapshot")
        assert (
            await restored.services.workspaces.read("from-snapshot", "nested/report.txt")
            == b"retained"
        )
        assert (await restored.services.accounts.list_users())[0].id == principal_id
    finally:
        await restored.close()
    with pytest.raises(OpsMeshError) as occupied:
        await restore_backup(
            target, bundle, expected_manifest_digest=manifest.manifest_digest, maintenance=True
        )
    assert occupied.value.code == "RESTORE_TARGET_NOT_EMPTY"


async def test_backup_and_restore_reject_maintenance_bypass_and_corruption_before_writes(tmp_path):
    settings, _, _ = await seed_platform(tmp_path / "original")
    bundle = tmp_path / "backup"
    with pytest.raises(OpsMeshError) as refused:
        await create_backup(settings, bundle)
    assert refused.value.code == "MAINTENANCE_REQUIRED" and not bundle.exists()
    manifest = await create_backup(settings, bundle, maintenance=True)
    (bundle / "workspaces" / "project" / "nested" / "report.txt").write_bytes(b"tampered")
    target = OpsMeshSettings(data_directory=tmp_path / "restore-target", worker_enabled=False)
    with pytest.raises(OpsMeshError) as corrupt:
        await restore_backup(
            target, bundle, expected_manifest_digest=manifest.manifest_digest, maintenance=True
        )
    assert corrupt.value.code == "CONTENT_CORRUPT"
    assert not target.data_directory.exists()


async def test_sqlite_restore_refuses_an_existing_database_outside_empty_data_directory(tmp_path):
    settings, _, _ = await seed_platform(tmp_path / "original")
    bundle = tmp_path / "backup"
    manifest = await create_backup(settings, bundle, maintenance=True)
    target = OpsMeshSettings(
        data_directory=tmp_path / "new-data",
        database_url=settings.resolved_database_url,
        worker_enabled=False,
    )
    with pytest.raises(OpsMeshError) as occupied:
        await restore_backup(
            target, bundle, expected_manifest_digest=manifest.manifest_digest, maintenance=True
        )
    assert occupied.value.code == "RESTORE_TARGET_NOT_EMPTY"
    assert not target.data_directory.exists()


async def test_application_refuses_start_with_restore_marker_even_if_schema_is_valid(tmp_path):
    settings = OpsMeshSettings(data_directory=tmp_path / "recovery", worker_enabled=False)
    application = OpsMeshApplication(settings)
    try:
        await application.initialize()
        await application.services.database.verify_schema()
        settings.data_directory.mkdir(parents=True, exist_ok=True)
        (settings.data_directory / ".restore-incomplete").write_text(
            "interrupted", encoding="ascii"
        )
        with pytest.raises(OpsMeshError) as incomplete:
            await application.start()
        assert incomplete.value.code == "RESTORE_INCOMPLETE"
    finally:
        await application.close()


async def test_offline_backup_does_not_require_worker_cluster_credentials(tmp_path):
    from opsmesh.infrastructure.run_workers.kubernetes.configuration import (
        KubernetesWorkerConfiguration,
    )

    settings, _, _ = await seed_platform(tmp_path / "original")
    configured = settings.model_copy(
        update={
            "run_worker_mode": "kubernetes",
            "run_worker_image": "unavailable-worker:1",
            "kubernetes_worker": KubernetesWorkerConfiguration(
                ca_file=tmp_path / "absent-ca", token_file=tmp_path / "absent-token"
            ),
        }
    )
    manifest = await create_backup(configured, tmp_path / "backup", maintenance=True)
    assert manifest.database_kind == "sqlite"


async def test_postgres_commands_keep_credentials_in_environment_and_restore_transactionally(
    tmp_path, monkeypatch
):
    calls = []

    class Process:
        returncode = 0

        async def wait(self):
            return self.returncode

    async def execute(*arguments, **options):
        calls.append((arguments, options))
        return Process()

    async def empty(url):
        pass

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    monkeypatch.setattr(database_backup, "require_empty_database", empty)
    url = "postgresql+asyncpg://backup_user:test-pass%40x@localhost:5432/backup_test"
    await database_backup.dump_database(url, tmp_path / "database.dump")
    await database_backup.restore_database(url, tmp_path / "database.dump")
    assert calls[0][0][0] == "pg_dump" and "--format=custom" in calls[0][0]
    assert calls[1][0][0] == "pg_restore" and "--single-transaction" in calls[1][0]
    assert "--exit-on-error" in calls[1][0] and "--clean" not in calls[1][0]
    for arguments, options in calls:
        assert all("test-pass" not in argument and "://" not in argument for argument in arguments)
        assert options["env"]["PGPASSWORD"] == "test-pass@x"
        assert options["env"]["PGHOST"] == "localhost"
        assert options["stderr"] == asyncio.subprocess.DEVNULL


async def test_postgres_timeout_kills_and_waits_before_reporting_safe_error(tmp_path, monkeypatch):
    killed = asyncio.Event()

    class Process:
        returncode = None

        def kill(self):
            self.returncode = -9
            killed.set()

        async def wait(self):
            await killed.wait()
            return self.returncode

    async def execute(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    with pytest.raises(OpsMeshError) as timeout:
        await database_backup.dump_database(
            "postgresql://test:password@localhost/test", tmp_path / "dump", timeout_seconds=0.01
        )
    assert timeout.value.code == "BACKUP_TIMEOUT" and killed.is_set()
    assert "password" not in str(timeout.value)


def test_backup_commands_require_external_digest_for_restore_and_verify():
    parser = _parser()
    backup = parser.parse_args(["backup", "new-directory", "--maintenance"])
    assert backup.command == "backup" and backup.maintenance
    restore = parser.parse_args(
        ["restore", "bundle", "--manifest-digest", "0" * 64, "--maintenance"]
    )
    assert restore.manifest_digest == "0" * 64
    with pytest.raises(SystemExit):
        parser.parse_args(["restore", "bundle", "--maintenance"])
