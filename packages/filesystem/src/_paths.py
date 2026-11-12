"""Resolve calling plugins and keep paths inside their roots."""

from __future__ import annotations

import inspect
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from opsmesh.plugins import registry

from .config import Settings
from .errors import FilesystemError, PathNotFound, PathRejected

_PACKAGE = "opsmesh.plugins.filesystem"

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_ILLEGAL = set('<>:"|?*')


@dataclass(frozen=True)
class PluginSpace:
    """The three file roots owned by one loaded plugin."""

    module_name: str
    data_dir: Path
    cache_dir: Path
    config_dir: Path

    @property
    def roots(self) -> tuple[Path, Path, Path]:
        """Return only the directories owned by this plugin."""
        return self.data_dir, self.cache_dir, self.config_dir


@dataclass(frozen=True)
class Roots:
    """Prepared filesystem roots shared by every plugin space."""

    data: Path
    cache: Path
    config: Path

    @classmethod
    def prepare(cls, root: Path) -> Roots:
        """Create the three root directories below configured storage."""
        base = root.expanduser().absolute()
        base.mkdir(parents=True, exist_ok=True)
        base = base.resolve()
        roots = cls(base / "data", base / "cache", base / "config")
        for directory in roots.entries():
            directory.mkdir(exist_ok=True)
        return roots

    def entries(self) -> tuple[Path, Path, Path]:
        return (self.data, self.cache, self.config)

    def for_plugin(self, module_name: str) -> PluginSpace:
        """Return the directory space owned by one loaded plugin."""
        parts = _module_parts(module_name)
        return PluginSpace(
            module_name=module_name,
            data_dir=self.data.joinpath(*parts),
            cache_dir=self.cache.joinpath(*parts),
            config_dir=self.config.joinpath(*parts),
        )


def resolve_caller_plugin() -> registry.Plugin:
    """Return the loaded plugin that invoked a public filesystem function."""
    plugin = registry._current_plugin.get()
    if plugin is not None and not _is_internal(plugin.module_name):
        return plugin
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            module_name = frame.f_globals.get("__name__", "")
            if module_name and not _is_internal(module_name):
                plugin = registry.get_plugin_by_module_name(module_name)
                if plugin is not None:
                    return plugin
            frame = frame.f_back
    finally:
        del frame
    raise FilesystemError("Cannot determine the calling plugin")


def relative_path_parts(
    relative: str,
    *,
    max_length: int,
    max_depth: int,
    error: type[Exception] = PathRejected,
) -> tuple[str, ...]:
    """Split a POSIX-style relative path and reject every escape form."""
    if not isinstance(relative, str):
        raise error("A relative path must be a string")
    stripped = relative[:-1] if relative.endswith("/") else relative
    if not stripped:
        raise error("A relative path must not be empty")
    if len(stripped) > max_length:
        raise error("The relative path is too long")
    if stripped.startswith(("/", "\\")):
        raise error("Absolute paths are not allowed")
    if len(stripped) >= 2 and stripped[1] == ":":
        raise error("Drive-qualified paths are not allowed")
    if "\\" in stripped:
        raise error("Backslash separators are not allowed")
    if "\x00" in stripped:
        raise error("NUL bytes are not allowed in paths")
    parts = tuple(stripped.split("/"))
    if len(parts) > max_depth:
        raise error("The relative path is too deep")
    for component in parts:
        _validate_component(component, error=error)
    return parts


def owned_path(
    space: PluginSpace,
    path: str | Path,
    *,
    settings: Settings,
    must_exist: bool,
) -> Path:
    """Resolve one caller path and prove it stays inside its plugin roots."""
    raw = Path(path) if isinstance(path, (str, Path)) else None
    if raw is None:
        raise PathRejected("A filesystem path must be a string or Path")
    if raw.is_absolute():
        candidate = raw
    else:
        parts = relative_path_parts(
            raw.as_posix(),
            max_length=settings.max_path_length,
            max_depth=settings.max_depth,
        )
        candidate = space.data_dir.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError as error:
        raise PathNotFound("The path does not exist") from error
    except OSError as error:
        raise FilesystemError("The path cannot be resolved") from error
    if not _within(space.roots, resolved):
        raise PathRejected("The path is outside the plugin file space")
    return resolved


def is_root(space: PluginSpace, path: Path) -> bool:
    """Report whether a resolved path is one of the plugin's own roots."""
    return path in space.roots


def _module_parts(module_name: str) -> tuple[str, ...]:
    if not isinstance(module_name, str) or not module_name:
        raise FilesystemError("A plugin module name is required")
    parts = tuple(module_name.split("."))
    for component in parts:
        if not component or component in {".", ".."}:
            raise FilesystemError("The plugin module name is not usable")
        if "/" in component or "\\" in component or "\x00" in component:
            raise FilesystemError("The plugin module name is not usable")
    return parts


def _is_internal(module_name: str) -> bool:
    return module_name == _PACKAGE or module_name.startswith(f"{_PACKAGE}.")


def _within(roots: Sequence[Path], candidate: Path) -> bool:
    return any(
        candidate == root or candidate.is_relative_to(root) for root in roots
    )


def _validate_component(component: str, *, error: type[Exception]) -> None:
    if not component or component in {".", ".."}:
        raise error("A path contains an invalid component")
    if len(component) > 255:
        raise error("A path contains an overly long component")
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in component
    ):
        raise error("A path contains control characters")
    if component != component.rstrip(" ."):
        raise error("A path has a trailing dot or space")
    if any(character in _WINDOWS_ILLEGAL for character in component):
        raise error("A path contains a Windows-illegal character")
    stem = component.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise error("A path uses a Windows-reserved name")
