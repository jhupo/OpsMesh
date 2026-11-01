"""Offline database/workspace/blob bundles with fail-closed restore targets."""

import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from opsmesh import __version__
from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.bootstrap.storage import create_blob_store
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.secrets import EnvironmentSecretResolver
from opsmesh.infrastructure.storage import BlobStore, LocalBlobStore
from opsmesh.infrastructure.storage.api import validate_digest
from opsmesh.infrastructure.storage.backup import export_blobs, restore_blobs, verify_blob_backup

from .database import database_kind, dump_database, require_empty_database, restore_database
from .files import (
    BackupDirectory,
    BackupFile,
    checked_file,
    copy_workspace_tree,
    fingerprint,
    restore_workspace_tree,
    verify_workspace_tree,
    write_manifest,
)


class PlatformBackupManifest(OpsMeshContract):
    format: Literal["opsmesh.platform.v1"] = "opsmesh.platform.v1"
    created_at: datetime
    opsmesh_version: str
    plugins: tuple[str, ...]
    database_kind: Literal["sqlite", "postgresql"]
    database: BackupFile
    blob_manifest_digest: str
    files: tuple[BackupFile, ...]
    directories: tuple[BackupDirectory, ...]

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    @property
    def manifest_digest(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()


def _require_maintenance(maintenance: bool, timeout_seconds: float) -> None:
    if not maintenance:
        raise OpsMeshError("MAINTENANCE_REQUIRED", "Stop all writers, workers and GC first")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise OpsMeshError("CONFIG_INVALID", "Backup timeout must be positive and finite")


def _offline_settings(settings: OpsMeshSettings) -> OpsMeshSettings:
    return settings.model_copy(
        update={
            "worker_enabled": False,
            "tracing_enabled": False,
            "run_worker_mode": "inline",
            "kubernetes_worker": None,
        }
    )


async def _check_active_directories(application: OpsMeshApplication) -> None:
    for workspace in await application.services.workspaces.list():
        await application.services.workspaces.root_path(workspace.id)


async def create_backup(
    settings: OpsMeshSettings,
    destination: Path,
    *,
    maintenance: bool = False,
    timeout_seconds: float = 600,
) -> PlatformBackupManifest:
    _require_maintenance(maintenance, timeout_seconds)
    if destination.exists() or destination.is_symlink():
        raise OpsMeshError("CONFLICT", "Backup destination must be a new directory")
    if destination.resolve().is_relative_to(settings.data_directory.resolve()):
        raise OpsMeshError(
            "CONFIG_INVALID", "Backup destination must be outside the data directory"
        )
    application = OpsMeshApplication(_offline_settings(settings))
    try:
        await application.services.database.verify_schema()
        await _check_active_directories(application)
        await asyncio.to_thread(destination.mkdir, parents=True)
        kind = database_kind(settings.resolved_database_url)
        database_name = "database.sqlite3" if kind == "sqlite" else "database.dump"
        await dump_database(
            settings.resolved_database_url,
            destination / database_name,
            timeout_seconds=timeout_seconds,
        )
        files, directories = await asyncio.to_thread(
            copy_workspace_tree,
            settings.data_directory / "storage" / "workspaces",
            destination / "workspaces",
        )
        blobs = await export_blobs(
            application.services.workspaces.blob_store, destination / "blobs", maintenance=True
        )
        manifest = PlatformBackupManifest(
            created_at=datetime.now(UTC),
            opsmesh_version=__version__,
            plugins=settings.plugins,
            database_kind=kind,
            database=await asyncio.to_thread(
                fingerprint, destination / database_name, database_name
            ),
            blob_manifest_digest=blobs.manifest_digest,
            files=files,
            directories=directories,
        )
        await asyncio.to_thread(write_manifest, destination / "manifest.json", manifest.to_bytes())
        return manifest
    finally:
        await application.close()


def _read_manifest(source: Path, expected_digest: str) -> PlatformBackupManifest:
    validate_digest(expected_digest)
    path = checked_file(source, "manifest.json")
    if source.is_symlink():
        raise OpsMeshError("BACKUP_INVALID", "Backup root cannot be a symlink")
    try:
        with path.open("rb") as stream:
            content = stream.read((64 << 20) + 1)
    except FileNotFoundError as error:
        raise OpsMeshError("BACKUP_INCOMPLETE", "Platform backup manifest is missing") from error
    if len(content) > 64 << 20 or hashlib.sha256(content).hexdigest() != expected_digest:
        raise OpsMeshError("CONTENT_CORRUPT", "Platform backup manifest digest mismatch")
    try:
        manifest = PlatformBackupManifest.model_validate_json(content)
    except ValueError as error:
        raise OpsMeshError("BACKUP_INVALID", "Platform backup manifest is invalid") from error
    filename = "database.sqlite3" if manifest.database_kind == "sqlite" else "database.dump"
    if manifest.database.path != filename:
        raise OpsMeshError("BACKUP_INVALID", "Unexpected database backup filename")
    return manifest


async def verify_backup(source: Path, *, expected_manifest_digest: str) -> PlatformBackupManifest:
    manifest = await asyncio.to_thread(_read_manifest, source, expected_manifest_digest)
    actual = await asyncio.to_thread(
        fingerprint, checked_file(source, manifest.database.path), manifest.database.path
    )
    if (actual.digest, actual.size) != (manifest.database.digest, manifest.database.size):
        raise OpsMeshError("CONTENT_CORRUPT", "Database backup digest mismatch")
    await asyncio.to_thread(
        verify_workspace_tree, source / "workspaces", manifest.files, manifest.directories
    )
    await verify_blob_backup(
        source / "blobs", expected_manifest_digest=manifest.blob_manifest_digest
    )
    return manifest


def _require_empty_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
        raise OpsMeshError("RESTORE_TARGET_NOT_EMPTY", "Restore data directory must be empty")


def _restore_store(settings: OpsMeshSettings) -> BlobStore:
    return create_blob_store(settings, EnvironmentSecretResolver()) or LocalBlobStore(
        settings.data_directory / "storage" / "objects"
    )


async def restore_backup(
    settings: OpsMeshSettings,
    source: Path,
    *,
    expected_manifest_digest: str,
    maintenance: bool = False,
    timeout_seconds: float = 600,
) -> PlatformBackupManifest:
    _require_maintenance(maintenance, timeout_seconds)
    manifest = await verify_backup(source, expected_manifest_digest=expected_manifest_digest)
    if manifest.opsmesh_version != __version__ or manifest.plugins != settings.plugins:
        raise OpsMeshError(
            "BACKUP_VERSION_MISMATCH", "Restore requires the recorded platform and plugin selection"
        )
    if database_kind(settings.resolved_database_url) != manifest.database_kind:
        raise OpsMeshError("FEATURE_UNSUPPORTED", "Cross-engine database restore is not supported")
    _require_empty_directory(settings.data_directory)
    await require_empty_database(settings.resolved_database_url)
    await asyncio.to_thread(settings.data_directory.mkdir, parents=True, exist_ok=True)
    marker = settings.data_directory / ".restore-incomplete"
    await asyncio.to_thread(write_manifest, marker, expected_manifest_digest.encode("ascii"))
    store = _restore_store(settings)
    try:
        if not await store.is_empty():
            raise OpsMeshError("RESTORE_TARGET_NOT_EMPTY", "Target blob namespace is not empty")
        await restore_blobs(
            store,
            source / "blobs",
            expected_manifest_digest=manifest.blob_manifest_digest,
            maintenance=True,
        )
        await asyncio.to_thread(
            restore_workspace_tree,
            source / "workspaces",
            settings.data_directory / "storage" / "workspaces",
            manifest.files,
            manifest.directories,
        )
        await restore_database(
            settings.resolved_database_url,
            checked_file(source, manifest.database.path),
            timeout_seconds=timeout_seconds,
        )
        application = OpsMeshApplication(_offline_settings(settings))
        try:
            await application.services.database.verify_schema()
            await _check_active_directories(application)
        finally:
            await application.close()
        await asyncio.to_thread(marker.unlink)
        return manifest
    finally:
        await store.close()
