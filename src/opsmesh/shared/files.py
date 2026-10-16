"""Bounded filesystem primitives: path validation, limited reads, atomic replacement.

These functions never decide a business root directory or grant access; callers
own authorization and pass explicit roots and limits.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

_MAX_PATH_SEGMENT_LENGTH = 255


def validate_relative_path(path: str) -> str:
    """Return a normalized POSIX relative path or raise for unsafe input."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("A relative path is required")
    if "\x00" in path:
        raise ValueError("Paths must not contain NUL")
    if path.startswith("/") or path.startswith("\\") or PurePosixPath(path).is_absolute():
        raise ValueError("Paths must be relative")
    if "\\" in path:
        raise ValueError("Paths must use forward slashes")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("Paths must not contain empty, current or parent segments")
    if any(len(segment) > _MAX_PATH_SEGMENT_LENGTH for segment in segments):
        raise ValueError("Path segments must not exceed 255 characters")
    return "/".join(segments)


def resolve_within_root(root: Path, relative_path: str) -> Path:
    """Resolve a validated relative path and refuse anything escaping the root.

    Symlinks are resolved before the containment check, so a link pointing
    outside the root is rejected rather than followed.
    """
    normalized = validate_relative_path(relative_path)
    root_resolved = root.resolve()
    candidate = (root_resolved / normalized).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError("Resolved path escapes the permitted root")
    return candidate


def read_bytes_bounded(path: Path, *, max_bytes: int) -> bytes:
    """Read at most max_bytes, rejecting larger files before allocating."""
    if max_bytes < 1:
        raise ValueError("A positive byte limit is required")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError("File exceeds the permitted size")
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("File grew beyond the permitted size while reading")
    return data


def read_text_bounded(path: Path, *, max_bytes: int, encoding: str = "utf-8") -> str:
    """Read a text file within a byte budget."""
    return read_bytes_bounded(path, max_bytes=max_bytes).decode(encoding)


def atomic_replace(path: Path, data: bytes) -> None:
    """Replace a file's content atomically; readers see old or new content only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".staging-")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def ensure_directory(path: Path) -> Path:
    """Create a directory (and parents) and return it resolved."""
    resolved = path.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved
