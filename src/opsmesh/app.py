"""Process entry point and plugin startup snapshot for the OpsMesh host.

The host owns the driver, the startup snapshot, and process lifecycle. It
does not implement protocols, interpret plugin settings, or construct agents.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from opsmesh.drivers.http import HTTPDriver
from opsmesh.framework import runtime
from opsmesh.framework.config import Config, Env
from opsmesh.framework.plugins import (
    PluginAdmission,
    PluginLoadDenied,
    PluginLoadRequest,
    PluginManager,
    PluginReloadError,
    clear_plugin_admission,
    register_plugin_manager,
    revoke_plugin,
    set_plugin_admission,
)
from opsmesh.framework.plugins.model import Plugin

logger = logging.getLogger(__name__)


class HostConfigurationError(ValueError):
    """The host manifest or startup snapshot is invalid."""


class HostStartupError(RuntimeError):
    """A required plugin could not be loaded, so the host must stop."""


class HTTPSettings(BaseModel):
    """Transport settings consumed by the concrete HTTP driver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)
    enabled: bool = True
    access_log: bool = False
    max_request_body: int = Field(default=1024 * 1024, ge=0)
    shutdown_timeout: float = Field(default=10.0, gt=0)


class PluginEntry(BaseModel):
    """One controlled plugin in the startup snapshot.

    Only the module name addresses the plugin. Display names, paths used for
    storage, and configuration namespaces never replace it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    module: str
    enabled: bool = True
    autoload: bool = True
    required: bool = False
    config_namespace: str | None = None
    config: dict[str, Any] | None = None
    expected_origin: str | None = None

    @field_validator("module")
    @classmethod
    def _validate_module(cls, value: str) -> str:
        parts = value.split(".")
        if not value or any(not part.isidentifier() for part in parts):
            raise ValueError("Plugin module must be a dotted Python name")
        return value

    @model_validator(mode="after")
    def _require_namespace_with_config(self) -> PluginEntry:
        if self.config is not None and not self.config_namespace:
            raise ValueError(
                "config_namespace is required when plugin config is supplied"
            )
        return self


class HostManifest(BaseModel):
    """Explicit startup snapshot supplied to the host."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: str = Field(default="development", pattern=r"^[a-zA-Z0-9_-]+$")
    http: HTTPSettings = Field(default_factory=HTTPSettings)
    plugin_paths: tuple[str, ...] = ()
    plugins: tuple[PluginEntry, ...] = ()

    @model_validator(mode="after")
    def _unique_modules_and_namespaces(self) -> HostManifest:
        modules: set[str] = set()
        namespaces: dict[str, dict[str, Any]] = {}
        for entry in self.plugins:
            if entry.module in modules:
                raise ValueError(f"Duplicate plugin module: {entry.module}")
            modules.add(entry.module)
            if entry.config is None:
                continue
            namespace = entry.config_namespace or ""
            previous = namespaces.get(namespace)
            if previous is not None and previous != entry.config:
                raise ValueError(
                    f"Conflicting config for namespace: {namespace}"
                )
            namespaces[namespace] = entry.config
        return self


def load_manifest(path: str | Path) -> HostManifest:
    """Read a JSON host manifest and resolve its declared paths.

    ``plugin_paths`` and ``expected_origin`` are resolved relative to the
    manifest directory so deployment location is not encoded in plugin code.
    """
    manifest_path = Path(path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = HostManifest.model_validate(data)
    base_dir = manifest_path.parent
    return manifest.model_copy(
        update={
            "plugin_paths": tuple(
                str(_resolve_path(item, base_dir))
                for item in manifest.plugin_paths
            ),
            "plugins": tuple(
                entry.model_copy(
                    update={
                        "expected_origin": (
                            str(_resolve_path(entry.expected_origin, base_dir))
                            if entry.expected_origin is not None
                            else None
                        )
                    }
                )
                for entry in manifest.plugins
            ),
        }
    )


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _build_admission(
    entries: Iterable[PluginEntry], base_dir: Path
) -> PluginAdmission:
    by_module = {entry.module: entry for entry in entries}

    def admission(request: PluginLoadRequest) -> bool:
        entry = by_module.get(request.module_name)
        if entry is None or not entry.enabled:
            return False
        if entry.expected_origin is None:
            return True
        expected = _resolve_path(entry.expected_origin, base_dir)
        if request.origin is None:
            raise PluginLoadDenied(
                f'Plugin "{entry.module}" has no source origin to verify'
            )
        actual = Path(request.origin).resolve()
        if actual != expected and expected not in actual.parents:
            raise PluginLoadDenied(
                f'Plugin "{entry.module}" loaded from unexpected origin: '
                f"{actual}"
            )
        return True

    return admission


@asynccontextmanager
async def load_host(
    manifest: HostManifest, *, manifest_dir: str | Path | None = None
) -> AsyncIterator[HTTPDriver]:
    """Load the controlled plugin snapshot without starting resources."""
    if runtime.is_initialized():
        raise HostStartupError("Framework runtime is already initialized")

    base_dir = Path(manifest_dir) if manifest_dir is not None else Path.cwd()
    for declared_path in manifest.plugin_paths:
        path = _resolve_path(declared_path, base_dir)
        if not path.is_dir():
            raise HostConfigurationError(
                f"Plugin path is not a directory: {path}"
            )
        import_path = str(path)
        if import_path not in sys.path:
            sys.path.insert(0, import_path)

    plugin_configs = {
        entry.config_namespace: dict(entry.config)
        for entry in manifest.plugins
        if entry.config is not None and entry.config_namespace is not None
    }
    driver = HTTPDriver(
        Env(environment=manifest.environment),
        Config(
            driver_options={"http": manifest.http.model_dump()},
            plugin_configs=plugin_configs,
        ),
    )
    runtime.set_driver(driver)
    set_plugin_admission(_build_admission(manifest.plugins, base_dir))

    loaded: list[Plugin] = []
    try:
        managers: dict[str, PluginManager] = {}
        for entry in manifest.plugins:
            manager = PluginManager(
                [entry.module],
                config=entry.config,
                namespace=entry.config_namespace,
                required=entry.required,
            )
            register_plugin_manager(manager)
            managers[entry.module] = manager

        for entry in manifest.plugins:
            if not (entry.enabled and entry.autoload):
                continue
            try:
                plugin = managers[entry.module].load_plugin(entry.module)
            except (PluginLoadDenied, PluginReloadError) as error:
                if entry.required:
                    raise HostStartupError(
                        f'Required plugin "{entry.module}" could not load'
                    ) from error
                logger.error(
                    'Plugin "%s" was rejected: %s', entry.module, error
                )
                continue
            if plugin is None:
                message = f'Plugin "{entry.module}" failed to load'
                if entry.required:
                    raise HostStartupError(
                        f'Required plugin "{entry.module}" failed to load'
                    )
                logger.error("%s; dependent entries are unavailable", message)
                continue
            loaded.append(plugin)

        yield driver
    finally:
        for plugin in reversed(loaded):
            try:
                revoke_plugin(plugin)
            except Exception:
                logger.exception(
                    'Failed to revoke plugin "%s" during shutdown',
                    plugin.id_,
                )
        clear_plugin_admission()


async def run_host(
    manifest: HostManifest,
    *,
    manifest_dir: str | Path | None = None,
    stop_event: anyio.Event | None = None,
) -> None:
    """Run loaded plugins until cancellation or an explicit stop event."""
    async with load_host(manifest, manifest_dir=manifest_dir) as driver:
        async with driver.lifespan():
            if stop_event is None:
                await anyio.sleep_forever()
            else:
                await stop_event.wait()


def main(argv: list[str] | None = None) -> int:
    """Keep the module entry point equivalent to the installed executable."""
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
