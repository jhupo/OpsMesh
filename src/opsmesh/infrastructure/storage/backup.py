"""Verified blob backup bundles; database and active workspace backups are separate."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract

from .api import BlobStore, validate_digest
from .local import LocalBlobStore


class BlobBackupEntry(OpsMeshContract):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class BlobBackupManifest(OpsMeshContract):
    format: Literal["opsmesh.blobs.v1"] = "opsmesh.blobs.v1"
    created_at: datetime
    source_identity: str
    objects: tuple[BlobBackupEntry, ...]

    @model_validator(mode="after")
    def unique_entries(self) -> "BlobBackupManifest":
        if len({entry.digest for entry in self.objects}) != len(self.objects):
            raise ValueError("Backup digests must be unique")
        return self

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    @property
    def manifest_digest(self) -> str:
        """Store this separately from the bundle to detect manifest tampering."""
        return hashlib.sha256(self.to_bytes()).hexdigest()


def _require_maintenance(maintenance: bool) -> None:
    if not maintenance:
        raise OpsMeshError(
            "MAINTENANCE_REQUIRED", "Stop writers and GC before exporting or restoring blobs"
        )


async def _inventory(store: BlobStore) -> set[str]:
    digests: set[str] = set()
    token: str | None = None
    while True:
        page = await store.list(continuation=token)
        digests.update(info.digest for info in page.items)
        if page.continuation is None:
            return digests
        if page.continuation == token:
            raise OpsMeshError("STORAGE_PROTOCOL_ERROR", "Blob listing did not advance")
        token = page.continuation


def _write_manifest(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(".incomplete")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


async def export_blobs(
    store: BlobStore,
    destination: Path,
    *,
    digests: set[str] | None = None,
    maintenance: bool = False,
    max_blob_bytes: int = 64 << 20,
) -> BlobBackupManifest:
    """Write a new bundle; absence of manifest.json marks an incomplete export."""
    _require_maintenance(maintenance)
    if destination.exists() or destination.is_symlink():
        raise OpsMeshError("CONFLICT", "Backup destination must be a new directory")
    selected = await _inventory(store) if digests is None else set(digests)
    for digest in selected:
        validate_digest(digest)
    await asyncio.to_thread(destination.mkdir, parents=True)
    backup = LocalBlobStore(destination / "objects", max_blob_bytes=max_blob_bytes)
    entries: list[BlobBackupEntry] = []
    for digest in sorted(selected):
        content = await store.get(digest)
        stored = await backup.put(content)
        if stored != digest:
            raise OpsMeshError("CONTENT_CORRUPT", "Source blob returned incorrect content")
        entries.append(BlobBackupEntry(digest=digest, size=len(content)))
    manifest = BlobBackupManifest(
        created_at=datetime.now(UTC), source_identity=store.identity, objects=tuple(entries)
    )
    await asyncio.to_thread(_write_manifest, destination / "manifest.json", manifest.to_bytes())
    return manifest


def _read_manifest(source: Path, expected_digest: str) -> BlobBackupManifest:
    validate_digest(expected_digest)
    path = source / "manifest.json"
    if source.is_symlink() or path.is_symlink():
        raise OpsMeshError("PATH_INVALID", "Backup paths cannot be symlinks")
    try:
        with path.open("rb") as stream:
            data = stream.read((64 << 20) + 1)
    except FileNotFoundError as error:
        raise OpsMeshError("BACKUP_INCOMPLETE", "Backup manifest is missing") from error
    if len(data) > 64 << 20 or hashlib.sha256(data).hexdigest() != expected_digest:
        raise OpsMeshError("CONTENT_CORRUPT", "Backup manifest digest mismatch")
    try:
        return BlobBackupManifest.model_validate_json(data)
    except ValueError as error:
        raise OpsMeshError("BACKUP_INVALID", "Backup manifest is invalid or unsupported") from error


async def verify_blob_backup(
    source: Path, *, expected_manifest_digest: str, max_blob_bytes: int = 64 << 20
) -> BlobBackupManifest:
    manifest = await asyncio.to_thread(_read_manifest, source, expected_manifest_digest)
    if not (source / "objects").is_dir():
        raise OpsMeshError("BACKUP_INCOMPLETE", "Backup object directory is missing")
    backup = LocalBlobStore(source / "objects", max_blob_bytes=max_blob_bytes)
    for entry in manifest.objects:
        content = await backup.get(entry.digest)
        if len(content) != entry.size:
            raise OpsMeshError("CONTENT_CORRUPT", "Backup object size mismatch")
    return manifest


async def restore_blobs(
    store: BlobStore,
    source: Path,
    *,
    expected_manifest_digest: str,
    maintenance: bool = False,
    max_blob_bytes: int = 64 << 20,
) -> BlobBackupManifest:
    """Validate the complete bundle first, then perform restartable immutable uploads."""
    _require_maintenance(maintenance)
    manifest = await verify_blob_backup(
        source, expected_manifest_digest=expected_manifest_digest, max_blob_bytes=max_blob_bytes
    )
    backup = LocalBlobStore(source / "objects", max_blob_bytes=max_blob_bytes)
    for entry in manifest.objects:
        content = await backup.get(entry.digest)
        if len(content) != entry.size or await store.put(content) != entry.digest:
            raise OpsMeshError("CONTENT_CORRUPT", "Restored blob does not match the manifest")
    return manifest
