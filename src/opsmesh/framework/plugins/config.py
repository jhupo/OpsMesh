"""Validate plugin settings from explicit deployment namespaces."""

import inspect
from copy import deepcopy
from typing import TypeVar

from pydantic import BaseModel

from opsmesh.framework.runtime import get_driver

from .registry import _current_plugin, get_plugin_by_module_name

C = TypeVar("C", bound=BaseModel)


def get_plugin_config(config: type[C], *, namespace: str) -> C:
    """Validate a copy of one plugin's startup configuration.

    Args:
        config: A plain Pydantic model declaring the plugin settings.
        namespace: The explicit key in Config.plugin_configs.

    Returns:
        A separately owned model. Missing namespaces use model defaults;
        missing required fields raise a validation error.

    Raises:
        TypeError: The model reads external settings instead of plain values.
        ValueError: The namespace is empty.
    """
    if not namespace.strip():
        raise ValueError("A plugin configuration namespace is required")
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
        del frame
    if plugin is not None and plugin.config_values is not None:
        if namespace != plugin.config_namespace:
            raise ValueError(
                "Plugin configuration belongs to another namespace"
            )
        values = plugin.config_values
    else:
        values = get_driver().config.plugin_configs.get(namespace, {})
    return config.model_validate(deepcopy(values))
