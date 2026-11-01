"""Filesystem operations run inside the workspace mutation lease."""

import stat
from pathlib import Path

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.workspaces.api import WorkspaceFileInfo

from .paths import checked_path


def file_info(root: Path, path: str) -> WorkspaceFileInfo:
    target = checked_path(root, path)
    if not target.exists():
        raise OpsMeshError("NOT_FOUND", "Workspace entry not found")
    if not target.is_file() and not target.is_dir():
        raise OpsMeshError("PATH_INVALID", "Workspace entry is not a regular file or directory")
    info = target.stat()
    return WorkspaceFileInfo(
        path=path,
        kind="directory" if target.is_dir() else "file",
        size=info.st_size if target.is_file() else 0,
        read_only=not bool(info.st_mode & stat.S_IWUSR),
    )


def list_tree(root: Path) -> tuple[WorkspaceFileInfo, ...]:
    return tuple(
        file_info(root, path.relative_to(root).as_posix()) for path in sorted(root.rglob("*"))
    )


def remove_file(root: Path, path: str) -> None:
    info = file_info(root, path)
    if info.kind != "file":
        raise OpsMeshError("PATH_INVALID", "File deletion does not recursively remove directories")
    if info.read_only:
        raise OpsMeshError("AUTHORIZATION_DENIED", "File is read-only")
    checked_path(root, path).unlink()


def rename_entry(root: Path, source: str, target: str) -> WorkspaceFileInfo:
    info = file_info(root, source)
    original, destination = checked_path(root, source), checked_path(root, target)
    if info.read_only:
        raise OpsMeshError("AUTHORIZATION_DENIED", "Entry is read-only")
    if destination.exists():
        raise OpsMeshError("CONFLICT", "Rename cannot overwrite another entry")
    if info.kind == "directory":
        if destination.is_relative_to(original):
            raise OpsMeshError("PATH_INVALID", "A directory cannot move inside itself")
        if any(item.read_only for item in list_tree(original)):
            raise OpsMeshError("AUTHORIZATION_DENIED", "Directory contains read-only files")
    if not destination.parent.is_dir():
        raise OpsMeshError("NOT_FOUND", "Rename target parent does not exist")
    original.rename(destination)
    return file_info(root, target)
