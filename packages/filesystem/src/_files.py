"""Bounded atomic file operations below prepared plugin roots."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO

from . import _archives as archives
from .config import Settings
from .errors import FilesystemError, PathNotFound, PathRejected

_AT_CURRENT_DIRECTORY = -100
_RENAME_NOREPLACE = 1


def write_bytes(path: Path, content: bytes, *, replace: bool) -> None:
    """Write one file atomically, creating its parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise PathRejected("The target file already exists") from error
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_bytes(path: Path) -> bytes:
    """Read one existing regular file."""
    try:
        if not path.is_file():
            raise PathNotFound("The path is not a regular file")
        return path.read_bytes()
    except FileNotFoundError as error:
        raise PathNotFound("The file does not exist") from error
    except OSError as error:
        raise FilesystemError("The file cannot be read") from error


def delete_path(path: Path) -> None:
    """Remove one file or directory tree."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError as error:
        raise PathNotFound("The path does not exist") from error
    except OSError as error:
        raise FilesystemError("The path cannot be removed") from error


def publish_directory(
    source: Path, destination: Path, settings: Settings
) -> None:
    """Copy a tree and publish it without replacing any existing target."""
    if not source.is_dir() or source.is_symlink():
        raise PathRejected("The source must be a regular directory")
    if source == destination or source in destination.parents:
        raise PathRejected("The destination cannot be inside the source")
    if destination.exists() or destination.is_symlink():
        raise PathRejected("The destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".publish-{uuid.uuid4().hex}"
    try:
        archives.copy_tree(source, staging, settings)
        _publish_new_directory(staging, destination)
    except OSError as error:
        raise FilesystemError("The directory cannot be published") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def extract_archive(
    stream: BinaryIO, destination: Path, settings: Settings
) -> None:
    """Extract one ZIP stream into a new destination directory."""
    if destination.exists():
        raise PathRejected("The extraction destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".extract-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        archive = staging / "archive.zip"
        archives.stage_stream(
            stream, archive, max_bytes=settings.max_archive_bytes
        )
        content = staging / "content"
        archives.extract_archive(archive, content, settings)
        _publish_new_directory(content, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def export_archive(source: Path, destination: Path) -> None:
    """Write a ZIP snapshot of one existing directory."""
    if not source.is_dir() or source.is_symlink():
        raise PathRejected("The source must be a regular directory")
    if destination.resolve().is_relative_to(source.resolve()):
        raise PathRejected("The export destination cannot be inside its source")
    archives.write_archive(source, destination)


def _publish_new_directory(source: Path, destination: Path) -> None:
    """Commit a staged directory using Linux's atomic no-replace rename."""
    if sys.platform != "linux":
        raise FilesystemError("Atomic directory publication requires Linux")
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    if (
        rename(
            _AT_CURRENT_DIRECTORY,
            os.fsencode(source),
            _AT_CURRENT_DIRECTORY,
            os.fsencode(destination),
            _RENAME_NOREPLACE,
        )
        == 0
    ):
        return
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise PathRejected("The destination already exists")
    raise OSError(code, os.strerror(code), str(destination))
