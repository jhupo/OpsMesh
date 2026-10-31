"""Import-time registration, ownership and explicit enable/disable policy."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from contextvars import ContextVar
from typing import Any

from opsmesh.framework.exception import PluginError

from .model import OpsMeshPlugin, OpsMeshPluginMetadata, PluginState

_current_plugin: ContextVar[OpsMeshPlugin | None] = ContextVar("opsmesh_plugin", default=None)


def get_current_plugin() -> OpsMeshPlugin | None:
    return _current_plugin.get()


class PluginManager:
    def __init__(self) -> None:
        self.plugins: dict[str, OpsMeshPlugin] = {}

    def load(self, name: str, *, config: dict[str, Any] | None = None) -> OpsMeshPlugin:
        parent = get_current_plugin()
        if existing := self.plugins.get(name):
            if existing.state == PluginState.LOADING:
                raise PluginError(f"Circular plugin require: {name}")
            if existing.state == PluginState.FAILED:
                raise PluginError(f"Failed plugin requires a process restart: {name}")
            if not existing.enabled:
                raise PluginError(f"Required plugin is disabled: {name}")
            if parent:
                parent.dependencies.add(name)
            return existing
        if name in sys.modules:
            raise PluginError(f"OpsMeshPlugin was imported before load_plugin: {name}")
        plugin = OpsMeshPlugin(
            name=name.rsplit(".", 1)[-1], module_name=name, config=dict(config or {})
        )
        self.plugins[name] = plugin
        before = set(sys.modules)
        token = _current_plugin.set(plugin)
        try:
            spec = importlib.util.find_spec(name)
            if spec is None or spec.loader is None:
                raise PluginError(f"Cannot find importable plugin: {name}")
            module = importlib.util.module_from_spec(spec)
            plugin.module = module
            module.__dict__["__plugin__"] = plugin
            sys.modules[name] = module
            spec.loader.exec_module(module)
            metadata = getattr(module, "__plugin_meta__", None)
            if metadata is not None and not isinstance(metadata, OpsMeshPluginMetadata):
                raise PluginError(f"Invalid __plugin_meta__ in {name}")
            plugin.metadata = metadata
            if metadata and metadata.config:
                metadata.config.model_validate(plugin.config)
            plugin.state = PluginState.ENABLED
        except BaseException:
            plugin.state = PluginState.FAILED
            try:
                plugin.rollback()
            finally:
                for key in set(sys.modules) - before:
                    if key == name or key.startswith(name + "."):
                        sys.modules.pop(key, None)
            raise
        finally:
            _current_plugin.reset(token)
        if parent:
            parent.dependencies.add(name)
        return plugin

    def disable(self, name: str, *, cascade: bool = False) -> None:
        if self.plugins[name].state not in (PluginState.ENABLED, PluginState.DISABLED):
            raise PluginError("Only a successfully loaded plugin can be disabled")
        dependents = [p for p in self.plugins.values() if p.enabled and name in p.dependencies]
        if dependents and not cascade:
            raise PluginError(
                f"Enabled plugins depend on {name}: {[p.module_name for p in dependents]}"
            )
        for dependent in dependents:
            self.disable(dependent.module_name, cascade=True)
        self.plugins[name].state = PluginState.DISABLED

    def enable(self, name: str) -> None:
        plugin = self.plugins[name]
        if plugin.state != PluginState.DISABLED:
            raise PluginError(f"OpsMeshPlugin is not disabled: {name}")
        if any(not self.plugins[d].enabled for d in plugin.dependencies):
            raise PluginError("Enable dependencies first")
        plugin.state = PluginState.ENABLED


manager = PluginManager()
