"""Validate settings owned by the calling plugin."""

import inspect
from copy import deepcopy
from typing import TypeVar

from pydantic import BaseModel

from .registry import _current_plugin, get_plugin_by_module_name

C = TypeVar("C", bound=BaseModel)


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
    return config.model_validate(deepcopy(values) if values is not None else {})
