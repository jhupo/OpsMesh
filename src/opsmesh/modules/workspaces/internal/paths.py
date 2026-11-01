"""Durable local content storage and workspace snapshots without archive extraction."""

import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError


def checked_path(root: Path, relative: str) -> Path:
    """Reject traversal, alternate separators, drive paths and symlink traversal."""
    posix = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or "\x00" in relative
        or ":" in relative
        or posix.is_absolute()
        or PureWindowsPath(relative).drive
        or any(part in {"..", "."} for part in relative.split("/"))
        or "" in relative.split("/")
    ):
        raise OpsMeshError("PATH_INVALID", "A safe workspace-relative file path is required")
    candidate = root.joinpath(*posix.parts)
    for parent in (candidate, *candidate.parents):
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise OpsMeshError("PATH_INVALID", "WorkspaceDescriptor symlinks are not accepted")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise OpsMeshError("PATH_INVALID", "Path escapes workspace")
    return candidate


def checked_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise OpsMeshError("CONFIG_INVALID", "Invalid workspace identifier")
    return value


def atomic_write(path: Path, data: bytes, *, read_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.stat().st_mode & stat.S_IWUSR:
        raise OpsMeshError("AUTHORIZATION_DENIED", "File is read-only")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if read_only:
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    finally:
        temporary.unlink(missing_ok=True)


def replace_managed_file(
    path: Path, data: bytes, *, expected_digest: str | None, read_only: bool
) -> None:
    """Called under a workspace mutation lease after management authorization."""
    if path.exists() and not path.is_file():
        raise OpsMeshError("CONFLICT", "Managed file path is occupied by a directory")
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if actual != expected_digest:
        raise OpsMeshError("CONFLICT", "Managed file changed since it was inspected")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    original_mode = path.stat().st_mode if path.exists() else None
    replaced = False
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if read_only:
            temporary.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        # Windows cannot replace a read-only destination. The owner-only API and
        # mutation lease enclose this temporary permission change and its rollback.
        if os.name == "nt" and original_mode is not None:
            path.chmod(original_mode | stat.S_IWUSR)
        os.replace(temporary, path)
        replaced = True
    finally:
        if not replaced and original_mode is not None and path.exists():
            path.chmod(original_mode)
        if temporary.exists():
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
            temporary.unlink()
