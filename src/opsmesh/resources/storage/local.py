"""Persistent local storage with descriptor-relative, no-symlink client file access.

The server storage root is trusted configuration. All paths beneath it are traversed
with directory descriptors so concurrent sandbox symlink changes cannot redirect I/O.
This backend requires POSIX directory-descriptor support, as used by Linux workers.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from opsmesh.resources.storage.base import LocalDirectoryStorage
from opsmesh.resources.storage.contracts import FileEntry
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.files import validate_relative_path
from opsmesh.shared.pagination import PageParams


class LocalFileStorage(LocalDirectoryStorage):
    def __init__(self, root: Path, *, max_transfer_bytes: int = 64 * 1024 * 1024) -> None:
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("Resource filesystem access requires POSIX directory descriptors")
        if max_transfer_bytes < 1:
            raise ValueError("The file transfer limit must be positive")
        self.root = root.resolve()
        try:
            self.root.mkdir(parents=True, mode=0o2770)
        except FileExistsError:
            if not self.root.is_dir():
                raise
        else:
            self.root.chmod(0o2770)
        self.max_transfer_bytes = max_transfer_bytes

    @contextmanager
    def _directory(
        self, storage_key: str, path: str = "", *, create: bool = False
    ) -> Iterator[int]:
        descriptors: list[int] = []
        try:
            parts = _parts(storage_key) + (_parts(path) if path else [])
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(descriptor)
            for part in parts:
                if create:
                    with suppress(FileExistsError):
                        _mkdir(descriptor, part)
                descriptor = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                descriptors.append(descriptor)
            yield descriptor
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise NotFoundError("The requested file or directory does not exist") from exc
            if exc.errno == errno.EEXIST:
                raise ConflictError("The target already exists") from exc
            if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EISDIR, errno.ENOTEMPTY}:
                raise InvalidArgumentError("The path has an unsupported file type") from exc
            if exc.errno in {errno.EACCES, errno.EPERM}:
                raise ForbiddenError("The storage operation is not permitted") from exc
            raise
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def ensure_directory(self, storage_key: str) -> None:
        with self._directory(storage_key, create=True):
            pass

    def local_directory(self, storage_key: str) -> Path:
        self.ensure_directory(storage_key)
        return self.root.joinpath(*_parts(storage_key))

    def list_entries(
        self, storage_key: str, *, path: str, page: PageParams
    ) -> tuple[list[FileEntry], int]:
        entries: list[FileEntry] = []
        with self._directory(storage_key, path) as descriptor, os.scandir(descriptor) as scan:
            for entry in scan:
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                kind: Literal["file", "directory", "symlink", "other"] = "other"
                if stat.S_ISLNK(metadata.st_mode):
                    kind = "symlink"
                elif stat.S_ISDIR(metadata.st_mode):
                    kind = "directory"
                elif stat.S_ISREG(metadata.st_mode):
                    kind = "file"
                entries.append(
                    FileEntry(
                        path=str(PurePosixPath(path) / entry.name),
                        name=entry.name,
                        kind=kind,
                        size=metadata.st_size,
                        modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC),
                    )
                )
        entries.sort(key=lambda entry: (entry.kind != "directory", entry.name))
        return entries[page.offset : page.offset + page.limit], len(entries)

    def read_file(self, storage_key: str, *, path: str) -> bytes:
        parts = _parts(path)
        with self._directory(storage_key, "/".join(parts[:-1])) as parent:
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise InvalidArgumentError("Only regular files may be downloaded")
                if metadata.st_nlink != 1:
                    raise InvalidArgumentError(
                        "Hard-linked files cannot be accessed through this API"
                    )
                if metadata.st_size > self.max_transfer_bytes:
                    raise InvalidArgumentError("The file exceeds the transfer limit")
                content = stream.read(self.max_transfer_bytes + 1)
                if len(content) > self.max_transfer_bytes:
                    raise InvalidArgumentError("The file exceeds the transfer limit")
                return content

    def write_file(self, storage_key: str, *, path: str, content: bytes) -> None:
        if len(content) > self.max_transfer_bytes:
            raise InvalidArgumentError("The file exceeds the transfer limit")
        parts = _parts(path)
        with self._directory(storage_key, "/".join(parts[:-1])) as parent:
            temporary = f".upload-{uuid4().hex}"
            # Separate service UIDs share this setgid tree through the private service group.
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode=0o660,
                dir_fd=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o660)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=parent)

    def create_subdirectory(self, storage_key: str, *, path: str) -> None:
        parts = _parts(path)
        with self._directory(storage_key, "/".join(parts[:-1])) as parent:
            _mkdir(parent, parts[-1])

    def delete_entry(self, storage_key: str, *, path: str) -> None:
        parts = _parts(path)
        with self._directory(storage_key, "/".join(parts[:-1])) as parent:
            metadata = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                os.rmdir(parts[-1], dir_fd=parent)
            else:
                os.unlink(parts[-1], dir_fd=parent)


def _mkdir(parent: int, name: str) -> None:
    os.mkdir(name, mode=0o2770, dir_fd=parent)
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        # Override the process umask only for new service-managed directories.
        # setgid retains the service GID for descendants created by either UID.
        os.fchmod(descriptor, 0o2770)
    finally:
        os.close(descriptor)


def _parts(path: str) -> list[str]:
    try:
        normalized = validate_relative_path(path)
    except ValueError as exc:
        raise InvalidArgumentError("A path must stay within the registered directory") from exc
    if len(normalized) > 1000 or any(ord(char) < 32 for char in normalized):
        raise InvalidArgumentError("The file path is invalid")
    return normalized.split("/")
