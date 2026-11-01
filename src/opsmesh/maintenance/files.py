"""Verified workspace tree copies without following links or extracting archives."""

import hashlib
import os
import shutil
import stat
from pathlib import Path, PurePosixPath

from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract


class BackupFile(OpsMeshContract):
    path: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)
    mode: int = Field(default=0o600, ge=0, le=0o777)


class BackupDirectory(OpsMeshContract):
    path: str
    mode: int = Field(ge=0, le=0o777)


def checked_file(root: Path, relative: str) -> Path:
    parts = relative.split("/")
    if (
        not relative
        or PurePosixPath(relative).is_absolute()
        or "\\" in relative
        or ":" in relative
        or "\x00" in relative
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise OpsMeshError("BACKUP_INVALID", "Backup contains an unsafe relative path")
    path = root.joinpath(*parts)
    for current in (path, *path.parents):
        if current.is_symlink():
            raise OpsMeshError("BACKUP_INVALID", "Backup paths cannot contain symlinks")
        if current == root:
            break
    return path


def fingerprint(path: Path, relative: str) -> BackupFile:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise OpsMeshError("BACKUP_INVALID", "Only regular files can be backed up")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    if size != metadata.st_size:
        raise OpsMeshError("BACKUP_CHANGED", "File changed while it was being backed up")
    return BackupFile(
        path=relative,
        digest=digest.hexdigest(),
        size=size,
        mode=stat.S_IMODE(metadata.st_mode) & 0o777,
    )


def copy_workspace_tree(
    source: Path, destination: Path
) -> tuple[tuple[BackupFile, ...], tuple[BackupDirectory, ...]]:
    if source.is_symlink():
        raise OpsMeshError("BACKUP_INVALID", "Workspace root cannot be a symlink")
    destination.mkdir(parents=True)
    files: list[BackupFile] = []
    directories: list[BackupDirectory] = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        checked_file(source, relative)
        target = checked_file(destination, relative)
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            target.mkdir(parents=True)
            directories.append(BackupDirectory(path=relative, mode=stat.S_IMODE(mode) & 0o777))
        elif stat.S_ISREG(mode):
            target.parent.mkdir(parents=True, exist_ok=True)
            expected = fingerprint(path, relative)
            shutil.copyfile(path, target)
            actual = fingerprint(target, relative)
            if actual.digest != expected.digest or actual.size != expected.size:
                raise OpsMeshError("BACKUP_CHANGED", "Workspace file changed during backup")
            files.append(expected)
        else:
            raise OpsMeshError("BACKUP_INVALID", "Workspace contains a non-regular entry")
    return tuple(files), tuple(directories)


def verify_workspace_tree(
    root: Path, files: tuple[BackupFile, ...], directories: tuple[BackupDirectory, ...]
) -> None:
    if root.is_symlink() or not root.is_dir():
        raise OpsMeshError("BACKUP_INCOMPLETE", "Workspace backup tree is missing")
    expected_paths = {entry.path for entry in files} | {entry.path for entry in directories}
    if len(expected_paths) != len(files) + len(directories):
        raise OpsMeshError("BACKUP_INVALID", "Workspace backup paths are duplicated")
    if {path.relative_to(root).as_posix() for path in root.rglob("*")} != expected_paths:
        raise OpsMeshError("CONTENT_CORRUPT", "Workspace backup inventory differs from manifest")
    for directory in directories:
        if not checked_file(root, directory.path).is_dir():
            raise OpsMeshError("BACKUP_INCOMPLETE", "Workspace backup directory is missing")
    for entry in files:
        actual = fingerprint(checked_file(root, entry.path), entry.path)
        if (actual.digest, actual.size) != (entry.digest, entry.size):
            raise OpsMeshError(
                "CONTENT_CORRUPT", "Workspace backup content does not match manifest"
            )


def restore_workspace_tree(
    source: Path,
    destination: Path,
    files: tuple[BackupFile, ...],
    directories: tuple[BackupDirectory, ...],
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise OpsMeshError("RESTORE_TARGET_NOT_EMPTY", "Workspace restore target is not empty")
    for directory in directories:
        checked_file(destination, directory.path).mkdir(parents=True, exist_ok=True)
    for entry in files:
        target = checked_file(destination, entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_file(source, entry.path), target)
        actual = fingerprint(target, entry.path)
        if (actual.digest, actual.size) != (entry.digest, entry.size):
            raise OpsMeshError("CONTENT_CORRUPT", "Workspace changed while restoring")
        target.chmod(entry.mode)
    for directory in sorted(directories, key=lambda item: item.path.count("/"), reverse=True):
        checked_file(destination, directory.path).chmod(directory.mode)


def write_manifest(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
