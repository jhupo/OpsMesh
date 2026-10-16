"""Bounded package ingestion: ZIP safety, digests and immutable blob storage.

Nothing here imports or executes package code: a package is read as bytes,
checked for unsafe members, digested and stored under its digest. Reading
verifies the digest again, so a stored package can never be swapped for
different content. The filesystem primitives come from ``shared.files``.
"""

from __future__ import annotations

import hashlib
import io
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.files import atomic_replace, read_bytes_bounded, resolve_within_root

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 4096
PACKAGE_NAMESPACE = "capability-packages"


class UnsafePackageError(InvalidArgumentError):
    """The package bytes violate the ingestion contract."""


@dataclass(frozen=True)
class StoredPackage:
    """Where one immutable package blob lives and what it digests to."""

    digest: str
    size: int
    storage_key: str


def package_digest(payload: bytes) -> str:
    """SHA-256 over the exact package bytes."""
    return hashlib.sha256(payload).hexdigest()


def read_archive(payload: bytes) -> dict[str, bytes]:
    """Read a ZIP into memory, refusing every unsafe member.

    Absolute paths, parent segments, backslashes, NUL bytes, symlinks,
    encrypted members, duplicate paths and oversized archives are all
    rejected before any byte is returned: a package is data, never a
    filesystem operation performed on the caller's behalf.
    """
    if not payload:
        raise UnsafePackageError("A package must contain at least one byte")
    if len(payload) > MAX_PACKAGE_BYTES:
        raise UnsafePackageError(
            "A package must not exceed the declared size",
        )
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            records = archive.infolist()
            if len(records) > MAX_PACKAGE_MEMBERS:
                raise UnsafePackageError("A package contains too many members")
            if sum(item.file_size for item in records) > MAX_EXPANDED_BYTES:
                raise UnsafePackageError("A package expands beyond the permitted size")
            files: dict[str, bytes] = {}
            for item in records:
                name = item.orig_filename
                if not _safe_member_name(name) or stat.S_ISLNK(item.external_attr >> 16):
                    raise UnsafePackageError("A package contains an unsafe member path")
                if item.flag_bits & 0x1:
                    raise UnsafePackageError("A package contains an encrypted member")
                if item.is_dir():
                    continue
                if name in files:
                    raise UnsafePackageError("A package contains duplicate member paths")
                files[name] = archive.read(item)
            if not files:
                raise UnsafePackageError("A package contains no files")
            return files
    except (BadZipFile, RuntimeError, OSError) as exc:
        raise UnsafePackageError("The package bytes are not a readable ZIP archive") from exc


def _safe_member_name(name: str) -> bool:
    if "\\" in name or "\x00" in name or ":" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute():
        return False
    return all(segment not in {"", ".", ".."} for segment in name.rstrip("/").split("/"))


def save_package(storage_root: Path, *, filename: str, payload: bytes) -> StoredPackage:
    """Store one package under its digest; the same digest is never rewritten."""
    digest = package_digest(payload)
    storage_key = f"{PACKAGE_NAMESPACE}/{digest}"
    target = resolve_within_root(storage_root, storage_key)
    if not target.exists():
        atomic_replace(target, payload)
    return StoredPackage(digest=digest, size=len(payload), storage_key=storage_key)


def build_package_archive(files: Mapping[str, bytes]) -> bytes:
    """Build one deterministic ZIP from an in-memory file mapping.

    Entry order, names and timestamps are fixed, so the same release
    content always produces the same bytes and therefore the same digest.
    """
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = ZipInfo(filename=path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[path])
    return buffer.getvalue()


def save_package_files(
    storage_root: Path, *, filename: str, files: Mapping[str, bytes]
) -> StoredPackage:
    """Store one release directory as a deterministic package archive."""
    return save_package(storage_root, filename=filename, payload=build_package_archive(files))


def read_package(
    storage_root: Path, stored: StoredPackage, *, max_bytes: int = MAX_PACKAGE_BYTES
) -> bytes:
    """Read one stored package back, refusing content that changed its digest."""
    path = resolve_within_root(storage_root, stored.storage_key)
    payload = read_bytes_bounded(path, max_bytes=max_bytes)
    if package_digest(payload) != stored.digest:
        raise UnsafePackageError("The stored package no longer matches its digest")
    return payload


def directory_digest(files: dict[str, bytes]) -> str:
    """Digest one directory tree as its canonical (path, content digest) listing."""
    listing = [
        {"path": path, "digest": package_digest(files[path]), "size": len(files[path])}
        for path in sorted(files)
    ]
    canonical = "\n".join(
        f"{entry['path']}\x00{entry['digest']}\x00{entry['size']}" for entry in listing
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_directory(root: Path, *, max_bytes: int = MAX_EXPANDED_BYTES) -> dict[str, bytes]:
    """Read one release directory into memory under a bounded total size.

    The bundled release source is a trusted read-only tree, but it is still
    read through the same bounds as an uploaded package so a corrupted
    release cannot exhaust the process.
    """
    resolved = root.resolve()
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(resolved.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(resolved).as_posix()
        payload = read_bytes_bounded(path, max_bytes=max_bytes - total)
        total += len(payload)
        if total > max_bytes:
            raise UnsafePackageError("The release directory exceeds the permitted size")
        files[relative] = payload
    if not files:
        raise UnsafePackageError("The release directory contains no files")
    return files
