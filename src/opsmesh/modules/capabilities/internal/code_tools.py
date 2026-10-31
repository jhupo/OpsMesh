"""Owned code declarations; persistent capability versions reference these callbacks."""

from functools import wraps
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import OpsMeshPlugin, get_current_plugin
from opsmesh.modules.capabilities.api import (
    ToolCallback,
)


class InProcessCodeToolRegistry:
    def __init__(self) -> None:
        self._callbacks: dict[str, ToolCallback] = {}
        self._owners: dict[str, OpsMeshPlugin | None] = {}
        self._frozen = False

    def register(self, code_ref: str, callback: ToolCallback) -> None:
        if self._frozen:
            raise OpsMeshError("REGISTRY_FROZEN", "Code tool declarations are frozen")
        if code_ref.startswith("resource:"):
            raise OpsMeshError("CONFIG_INVALID", "Resource code references are reserved")
        self._register(code_ref, callback)

    def register_runtime(self, code_ref: str, callback: ToolCallback) -> None:
        """Register a platform-generated resource closure, including after declarations freeze."""
        if not code_ref.startswith("resource:") or len(code_ref.split(":")) < 3:
            raise OpsMeshError("CONFIG_INVALID", "Runtime code references require resource scope")
        self._register(code_ref, callback)

    def unregister_runtime(self, code_ref: str) -> None:
        """Forget a generated run closure; persistent tool definitions remain auditable."""
        if not code_ref.startswith("resource:"):
            raise OpsMeshError("CONFIG_INVALID", "Only generated resource code can be removed")
        self._callbacks.pop(code_ref, None)
        self._owners.pop(code_ref, None)

    def _register(self, code_ref: str, callback: ToolCallback) -> None:
        if not code_ref or code_ref in self._callbacks:
            raise OpsMeshError("CONFLICT", "Code tool reference already registered or empty")
        owner = get_current_plugin()

        @wraps(callback)
        async def invoke(arguments: dict[str, Any]) -> Any:
            if code_ref.startswith("resource:") and self._callbacks.get(code_ref) is not invoke:
                raise OpsMeshError("RESOURCE_RELEASED", "Generated resource code has been released")
            if owner is not None and not owner.enabled:
                raise OpsMeshError("PLUGIN_DISABLED", "Code tool plugin is not enabled")
            return await callback(arguments)

        self._callbacks[code_ref] = invoke
        self._owners[code_ref] = owner
        if owner is not None:

            def rollback() -> None:
                if self._callbacks.get(code_ref) is invoke:
                    del self._callbacks[code_ref]
                    self._owners.pop(code_ref, None)

            owner.own(rollback)

    def resolve(self, code_ref: str) -> ToolCallback:
        owner = self._owners.get(code_ref)
        if owner is not None and not owner.enabled:
            raise OpsMeshError("PLUGIN_DISABLED", "Code tool plugin is not enabled")
        try:
            return self._callbacks[code_ref]
        except KeyError as error:
            raise OpsMeshError("NOT_FOUND", "Code tool is not deployed") from error

    def freeze(self) -> None:
        self._frozen = True
