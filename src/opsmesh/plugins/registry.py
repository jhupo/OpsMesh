from threading import RLock

from opsmesh.contracts.registration import PluginContext
from opsmesh.shared.errors import NotFoundError


class PluginRegistry:
    def __init__(self) -> None:
        self.items: dict[str, PluginContext] = {}
        self.load_lock = RLock()

    def get(self, installation_id: str) -> PluginContext:
        context = self.items.get(installation_id)
        if context is None:
            raise NotFoundError("Plugin version is not loaded")
        return context
