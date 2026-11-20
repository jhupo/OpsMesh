"""Validate plugin configuration and prepare adapters before Driver.run."""

import json
from collections.abc import Collection
from pathlib import Path, PureWindowsPath
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from opsmesh.adapters import Adapter
from opsmesh.compat import type_validate_python
from opsmesh.drivers import Driver

from . import _get_global_plugin_config
from .model import Plugin

C = TypeVar("C", bound=BaseModel)


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def _read_document(plugin: Plugin, filename: str) -> dict[str, Any]:
    origin = getattr(plugin.module, "__file__", None)
    if not origin:
        raise ValueError(
            f"{plugin.id_}: plugin entry has no filesystem location"
        )
    label = f"{plugin.id_}: {filename}"
    try:
        root = Path(origin).resolve().parent
        relative = Path(filename)
        if (
            not relative.parts
            or relative.is_absolute()
            or PureWindowsPath(filename).anchor
            or "\\" in filename
            or ".." in relative.parts
        ):
            raise ValueError("use a relative path inside the plugin")
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError("config_file resolves outside the plugin")
        if not target.is_file():
            raise ValueError("config_file is missing or is not a regular file")
        document = json.loads(
            target.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError(f"{label}: {exc}") from None
    document = _object(document, label)
    if unexpected := document.keys() - {"config", "dependencies"}:
        keys = ", ".join(sorted(unexpected))
        raise ValueError(f"{label}: unknown keys: {keys}")
    return document


def _invalid(path: str, exc: ValidationError) -> ValueError:
    fields = [
        ".".join(map(str, error["loc"])) + f" ({error['type']})"
        for error in exc.errors()
    ]
    return ValueError(f"{path}: invalid configuration: {', '.join(fields)}")


def _validate(model: type[C], value: Any, path: str) -> C:
    try:
        return type_validate_python(model, _object(value, path))
    except ValidationError as exc:
        raise _invalid(path, exc) from None


def read_file_config(plugin: Plugin, filename: str, model: type[C]) -> C:
    """Read and validate the plugin's own config without storing it."""
    document = _read_document(plugin, filename)
    return _validate(model, document.get("config", {}), f"{plugin.id_}.config")


def _dependencies(plugin: Plugin, document: dict[str, Any]) -> dict[str, Any]:
    path = f"{plugin.id_}.dependencies"
    return _object(document.get("dependencies", {}), path)


def read_consumer_configs(
    provider_id: str, model: type[C], plugins: Collection[Plugin]
) -> dict[str, C]:
    """Read the provider's entries from enabled consumers' config files."""
    configs: dict[str, C] = {}
    for plugin in sorted(plugins, key=lambda item: item.id_):
        metadata = plugin.metadata
        if not plugin.enabled or metadata is None:
            continue
        filename = metadata.config_file
        if filename is None:
            continue
        document = _read_document(plugin, filename)
        dependencies = _dependencies(plugin, document)
        if provider_id in dependencies:
            path = f"{plugin.id_}.dependencies.{provider_id}"
            value = _validate(model, dependencies[provider_id], path)
            configs[plugin.id_] = value
    return configs


def prepare_plugins(
    driver: Driver, plugins: Collection[Plugin], existing: Collection[str]
) -> None:
    """Validate plugin and consumer configs, then register declared adapters."""
    enabled = sorted((p for p in plugins if p.enabled), key=lambda p: p.id_)
    active = {p.id_: p for p in enabled}
    configs: dict[str, BaseModel] = {}
    bindings: dict[str, dict[str, BaseModel]] = {}
    for plugin_id, plugin in active.items():
        metadata = plugin.metadata
        if metadata is None or metadata.config_file is None:
            continue
        document = _read_document(plugin, metadata.config_file)
        raw = _object(document.get("config", {}), f"{plugin_id}.config")
        if metadata.config is not None:
            configs[plugin_id] = _validate(
                metadata.config, raw, f"{plugin_id}.config"
            )
        elif raw:
            raise ValueError(f"{plugin_id}.config: no config model declared")
        for provider_id, value in _dependencies(plugin, document).items():
            path = f"{plugin_id}.dependencies.{provider_id}"
            provider = active.get(provider_id)
            if provider is None:
                raise ValueError(f"{path}: provider is not loaded and enabled")
            spec = provider.metadata
            if spec is None or spec.dependency_config is None:
                raise ValueError(
                    f"{path}: provider does not accept dependency config"
                )
            bindings.setdefault(provider_id, {})[plugin_id] = _validate(
                spec.dependency_config, value, path
            )

    plans: list[tuple[type[Adapter], dict[str, Any]]] = []
    names = set(existing)
    for plugin_id, plugin in active.items():
        metadata = plugin.metadata
        if metadata is None or (adapter := metadata.adapter) is None:
            continue
        if not isinstance(adapter, type) or not issubclass(adapter, Adapter):
            raise TypeError(
                f"{plugin_id}: metadata.adapter must be an Adapter class"
            )
        name = adapter.get_name()
        if name in names:
            raise ValueError(
                f"{plugin_id}: adapter protocol {name!r} is already registered"
            )
        names.add(name)
        prepare = getattr(adapter, "prepare_settings", None)
        if not callable(prepare):
            raise TypeError(f"{plugin_id}: adapter requires prepare_settings")
        settings = configs.get(plugin_id)
        try:
            if metadata.config is not None and settings is None:
                settings = _get_global_plugin_config(metadata.config)
            kwargs = prepare(driver, settings, bindings.get(plugin_id, {}))
        except ValidationError as exc:
            raise _invalid(f"{plugin_id}.config", exc) from None
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{plugin_id}: {exc}") from None
        if not isinstance(kwargs, dict):
            raise TypeError(f"{plugin_id}: prepare_settings must return a dict")
        plans.append((adapter, kwargs))

    for adapter, kwargs in plans:
        driver.register_adapter(adapter, **kwargs)
