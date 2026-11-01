"""Load modules explicitly; require returns ordinary Python modules."""

from __future__ import annotations

from contextlib import suppress
from types import ModuleType
from typing import Any, TypeVar

from pydantic import BaseModel

from opsmesh.framework.exception import ContextUnavailableError

from .manager import get_current_plugin, manager
from .model import OpsMeshPlugin

T = TypeVar("T", bound=BaseModel)


def load_plugin(name: str, *, config: dict[str, Any] | None = None) -> OpsMeshPlugin:
    return manager.load(name, config=config)


def load_plugins(*names: str) -> tuple[OpsMeshPlugin, ...]:
    return tuple(load_plugin(name) for name in names)


def require(name: str) -> ModuleType:
    plugin = load_plugin(name)
    if plugin.module is None:
        raise RuntimeError("OpsMeshPlugin has no module")
    return plugin.module


def get_plugin_config(config_type: type[T], *, plugin_name: str | None = None) -> T:
    plugin = manager.plugins.get(plugin_name) if plugin_name else get_current_plugin()
    if plugin is None:
        from opsmesh.framework.matcher import get_current_matcher

        with suppress(ContextUnavailableError):
            plugin = get_current_matcher().owner
    if plugin is None:
        raise ContextUnavailableError("No plugin context; specify plugin_name")
    return config_type.model_validate(plugin.config)
