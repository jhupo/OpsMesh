"""Per-plugin data, cache, and config directories with file operations."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import BinaryIO

from anyio import to_thread

from opsmesh.framework import on_shutdown, on_startup
from opsmesh.framework.plugins.config import get_plugin_config

from . import _files as files
from ._paths import (
    PluginSpace,
    Roots,
    is_root,
    owned_path,
    relative_path_parts,
    resolve_caller_plugin,
)
from .config import CONFIG_NAMESPACE, Settings
from .errors import (
    ArchiveRejected,
    FilesystemClosed,
    PathRejected,
)

_settings: Settings | None = None
_roots: Roots | None = None


@dataclass(frozen=True)
class ArchiveExport:
    """A ZIP snapshot that the caller must discard after streaming."""

    path: Path

    def discard(self) -> None:
        """Remove the temporary export file."""
        self.path.unlink(missing_ok=True)


def data_dir() -> Path:
    """Return the calling plugin's persistent data directory."""
    return _space().data_dir


def data_file(name: str) -> Path:
    """Return a file path inside the calling plugin's data directory."""
    return _child(_space().data_dir, name)


def cache_dir() -> Path:
    """Return the calling plugin's disposable cache directory."""
    return _space().cache_dir


def cache_file(name: str) -> Path:
    """Return a file path inside the calling plugin's cache directory."""
    return _child(_space().cache_dir, name)


def config_dir() -> Path:
    """Return the calling plugin's local configuration directory."""
    return _space().config_dir


def config_file(name: str) -> Path:
    """Return a file path inside the calling plugin's config directory."""
    return _child(_space().config_dir, name)


async def write_file(
    path: str | Path,
    content: bytes | bytearray | str,
    *,
    replace: bool = True,
) -> Path:
    """Write one file atomically and return its resolved path."""
    space, settings = _space(), _settings_value()
    target = owned_path(space, path, settings=settings, must_exist=False)
    if is_root(space, target):
        raise PathRejected("A plugin root cannot be overwritten")
    data = _content_bytes(content)
    await to_thread.run_sync(
        partial(files.write_bytes, replace=replace), target, data
    )
    return target


async def read_file(path: str | Path) -> bytes:
    """Read one regular file inside the calling plugin's file space."""
    target = _owned(path, must_exist=True)
    return await to_thread.run_sync(files.read_bytes, target)


async def delete(path: str | Path) -> None:
    """Remove one file or directory tree inside the plugin file space."""
    space = _space()
    target = _owned(path, must_exist=True)
    if is_root(space, target):
        raise PathRejected("A plugin root cannot be deleted")
    await to_thread.run_sync(files.delete_path, target)


async def publish_directory(
    source: str | Path, destination: str | Path
) -> Path:
    """Atomically publish a copied tree at a destination that does not exist."""
    space, settings = _space(), _settings_value()
    origin = owned_path(space, source, settings=settings, must_exist=True)
    target = owned_path(space, destination, settings=settings, must_exist=False)
    if is_root(space, target):
        raise PathRejected("A plugin root cannot be republished")
    await to_thread.run_sync(files.publish_directory, origin, target, settings)
    return target


async def extract_archive(
    stream: BinaryIO | bytes | bytearray,
    destination: str | Path,
) -> Path:
    """Extract one ZIP stream into a new directory in the plugin space."""
    space, settings = _space(), _settings_value()
    target = owned_path(space, destination, settings=settings, must_exist=False)
    if is_root(space, target):
        raise PathRejected("An archive cannot be extracted onto a root")
    source = _binary(stream)
    await to_thread.run_sync(files.extract_archive, source, target, settings)
    return target


async def export_archive(source: str | Path) -> ArchiveExport:
    """Snapshot a directory outside the export cache into a temporary ZIP."""
    space, settings = _space(), _settings_value()
    origin = owned_path(space, source, settings=settings, must_exist=True)
    destination = owned_path(
        space,
        space.cache_dir / ".exports" / f"{uuid.uuid4().hex}.zip",
        settings=settings,
        must_exist=False,
    )
    await to_thread.run_sync(files.export_archive, origin, destination)
    return ArchiveExport(destination)


@on_startup
async def _startup() -> None:
    global _roots, _settings
    settings = get_plugin_config(Settings, namespace=CONFIG_NAMESPACE)
    _roots = Roots.prepare(settings.root)
    _settings = settings


@on_shutdown
async def _shutdown() -> None:
    global _roots, _settings
    _roots = None
    _settings = None


def _settings_value() -> Settings:
    if _settings is None:
        raise FilesystemClosed("The filesystem plugin is not running")
    return _settings


def _space() -> PluginSpace:
    roots = _roots
    if roots is None:
        raise FilesystemClosed("The filesystem plugin is not running")
    return roots.for_plugin(resolve_caller_plugin().module_name)


def _owned(path: str | Path, *, must_exist: bool) -> Path:
    return owned_path(
        _space(), path, settings=_settings_value(), must_exist=must_exist
    )


def _child(base: Path, name: str) -> Path:
    settings = _settings_value()
    parts = relative_path_parts(
        name,
        max_length=settings.max_path_length,
        max_depth=settings.max_depth,
    )
    return base.joinpath(*parts)


def _content_bytes(content: bytes | bytearray | str) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    raise PathRejected("File content must be bytes or text")


def _binary(stream: BinaryIO | bytes | bytearray) -> BinaryIO:
    if isinstance(stream, (bytes, bytearray)):
        return io.BytesIO(bytes(stream))
    if not hasattr(stream, "read"):
        raise ArchiveRejected("The archive must be a binary stream")
    return stream
