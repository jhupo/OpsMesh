"""Consistent offline snapshots of published project bindings."""

import shutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.coordination.process_lock import LocalProcessLock

from .files import BackupDirectory, BackupFile, checked_file, fingerprint


@contextmanager
def lock_project_queues(data_directory: Path) -> Iterator[None]:
    root = data_directory / "conversations"
    with ExitStack() as resources:
        if root.is_symlink():
            raise OpsMeshError("BACKUP_INVALID", "Conversation root cannot be a symlink")
        for directory in sorted(root.iterdir()) if root.exists() else ():
            checked_file(root, directory.name)
            if not directory.is_dir():
                raise OpsMeshError("BACKUP_INVALID", "Conversation entry must be a directory")
            checked_file(directory, "consumer.lock")
            lock = LocalProcessLock(directory / "consumer.lock")
            try:
                lock.acquire()
            except RuntimeError:
                raise OpsMeshError(
                    "MAINTENANCE_REQUIRED", "Stop conversation consumers before backup"
                ) from None
            resources.callback(lock.release)
        yield


def export_project_state(
    data_directory: Path, destination: Path
) -> tuple[tuple[BackupFile, ...], tuple[BackupDirectory, ...]]:
    destination.mkdir(mode=0o700)
    source = data_directory / "projects"
    if source.is_symlink():
        raise OpsMeshError("BACKUP_INVALID", "Project state root cannot be a symlink")
    for entry in sorted(source.iterdir()) if source.exists() else ():
        checked_file(source, entry.name)
        if not entry.is_file() or entry.suffix != ".json":
            raise OpsMeshError("BACKUP_INVALID", "Unexpected project state entry")
        target = destination / "projects" / entry.name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        expected = fingerprint(entry, entry.name)
        shutil.copyfile(entry, target)
        if fingerprint(target, entry.name).digest != expected.digest:
            raise OpsMeshError("BACKUP_CHANGED", "Project binding changed during backup")
    files, directories = [], []
    for path in sorted(destination.rglob("*")):
        relative = path.relative_to(destination).as_posix()
        if path.is_dir():
            path.chmod(0o700)
            directories.append(BackupDirectory(path=relative, mode=0o700))
        else:
            path.chmod(0o600)
            files.append(fingerprint(path, relative))
    return tuple(files), tuple(directories)
