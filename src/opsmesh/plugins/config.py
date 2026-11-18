"""Validate settings owned by the calling plugin."""

import inspect
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
)

from .model import Plugin
from .registry import _current_plugin, get_plugin, get_plugin_by_module_name

C = TypeVar("C", bound=BaseModel)


class PluginConfiguration(BaseModel):
    """Plugin values and consumer-owned configuration for dependencies."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )

    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    dependencies: dict[str, dict[str, JsonValue]] = Field(
        default_factory=dict, repr=False
    )


def get_plugin_config(config: type[C]) -> C:
    """Validate a copy of one plugin's startup configuration.

    Args:
        config: A plain Pydantic model declaring the plugin settings.

    Returns:
        A separately owned model. Missing configuration uses model defaults;
        missing required fields raise a validation error.

    Raises:
        TypeError: The model reads external settings instead of plain values.
        RuntimeError: The caller does not belong to a loaded plugin.
    """
    if any(
        "_settings_build_values" in base.__dict__ for base in config.__mro__
    ):
        raise TypeError("Plugin configuration must use a plain BaseModel")
    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame is not None else None
        provider = (
            get_plugin_by_module_name(caller.f_globals.get("__name__", ""))
            if caller is not None
            else None
        )
        plugin = provider or _current_plugin.get()
    finally:
        del caller
        del frame
    if plugin is None:
        raise RuntimeError(
            "Plugin configuration requires a loaded plugin owner"
        )
    values = plugin.config_values
    return config.model_validate(
        deepcopy(values) if values is not None else {}
    )


def _dependency_provider(owner: Plugin, name: str) -> Plugin:
    provider = get_plugin(name) or get_plugin_by_module_name(name)
    if (
        provider is None
        or provider.id_ not in owner.dependencies
        or name not in (provider.id_, provider.module_name)
    ):
        raise ValueError(
            f"{owner.module_name}.dependencies.{name}: "
            "dependency is not declared"
        )
    if (
        provider.metadata is None
        or provider.metadata.dependency_config is None
    ):
        raise ValueError(
            f"{owner.module_name}.dependencies.{name}: "
            "provider has no configuration schema"
        )
    return provider


def validate_dependency_config(
    owner: Plugin, values: dict[str, dict[str, JsonValue]] | None = None
) -> None:
    """Validate only declared dependencies, without importing new providers."""
    configured = owner.dependency_config_values if values is None else values
    seen: set[str] = set()
    for name, raw in configured.items():
        provider = _dependency_provider(owner, name)
        if provider.id_ in seen:
            raise ValueError(
                f"{owner.module_name}.dependencies.{name}: "
                "duplicate dependency alias"
            )
        seen.add(provider.id_)
        try:
            provider.metadata.dependency_config.model_validate(deepcopy(raw))
        except ValidationError as error:
            locations = ", ".join(
                (
                    ".".join(str(part) for part in item["loc"])
                    or "configuration"
                )
                + ": "
                + item["msg"]
                for item in error.errors(include_input=False)
            )
            raise ValueError(
                f"{owner.module_name}.dependencies.{name}: invalid {locations}"
            ) from None


def get_dependency_config(config: type[C], owner: Plugin | None = None) -> C:
    """Read the provider's typed settings for the current consuming plugin.

    Args:
        config: The provider's schema declared in PluginMetadata.
        owner: Consumer being inspected during a plugin-load notification.
            Startup callbacks use their current plugin scope by default.

    Returns:
        A separately owned model, using defaults when no values were supplied.
    """
    consumer = owner or _current_plugin.get()
    provider = get_plugin_by_module_name(config.__module__)
    if consumer is None or provider is None:
        raise RuntimeError("Dependency configuration requires loaded plugins")
    if (
        provider.id_ not in consumer.dependencies
        or provider.metadata is None
        or provider.metadata.dependency_config is not config
    ):
        raise ValueError("The schema must belong to a declared dependency")
    validate_dependency_config(consumer)
    for name, raw in consumer.dependency_config_values.items():
        if _dependency_provider(consumer, name) is provider:
            return config.model_validate(deepcopy(raw))
    return config.model_validate({})


def read_config_file(plugin: Plugin) -> PluginConfiguration:
    """Read JSON from the plugin entry directory without following escapes.

    Args:
        plugin: Loaded declarations whose metadata names the configuration.

    Returns:
        Own settings and dependency settings ready for schema validation.

    Raises:
        ValueError: No file is declared or the path escapes the plugin root.
        OSError: The declared file cannot be read.
    """
    name = plugin.metadata.config_file if plugin.metadata else None
    if not name:
        raise ValueError("The plugin does not declare a configuration file")
    relative = PureWindowsPath(name)
    if relative.drive or relative.root or ".." in relative.parts:
        raise ValueError(
            "Configuration file must stay within the plugin package"
        )
    origin = getattr(plugin.module, "__file__", None)
    if origin is None:
        raise ValueError("The plugin has no filesystem entry directory")
    root = Path(origin).resolve().parent
    path = root.joinpath(*relative.parts).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError(
            "Configuration file resolves outside the plugin package"
        )
    return PluginConfiguration.model_validate_json(
        path.read_text(encoding="utf-8")
    )
