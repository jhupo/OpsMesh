"""Public adapter base; protocol resources use the driver lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opsmesh.framework.errors import OpsMeshError

from .bot import OpsMeshBot
from .event import OpsMeshEvent

if TYPE_CHECKING:
    from opsmesh.framework.drivers import OpsMeshDriver
    from opsmesh.framework.message import DispatchResult
    from opsmesh.framework.plugin import OpsMeshPlugin


class OpsMeshAdapter:
    name = "adapter"
    required_capabilities: frozenset[str] = frozenset()

    def __init__(self, driver: OpsMeshDriver) -> None:
        self.driver = driver
        self.bots: dict[str, OpsMeshBot] = {}
        self._plugin_owner: OpsMeshPlugin | None = None
        self._registration_removed = False

    @property
    def enabled(self) -> bool:
        return not self._registration_removed and (
            self._plugin_owner is None or self._plugin_owner.enabled
        )

    def ensure_enabled(self) -> None:
        if self._registration_removed:
            raise OpsMeshError("BOT_ADAPTER_UNAVAILABLE", "Bot adapter registration was removed")
        if not self.enabled:
            raise OpsMeshError("PLUGIN_DISABLED", "The bot adapter's owning plugin is disabled")

    def _bind_registration(self, owner: OpsMeshPlugin | None) -> None:
        self._plugin_owner = owner
        self._registration_removed = False

    def _remove_registration(self) -> None:
        self._plugin_owner = None
        self._registration_removed = True

    def connect(self, bot: OpsMeshBot) -> None:
        self.ensure_enabled()
        if bot.adapter is not self:
            raise ValueError("OpsMeshBot belongs to another adapter")
        if bot.ref.bot_id in self.bots:
            raise ValueError(f"OpsMeshBot already connected: {bot.ref.bot_id}")
        self.bots[bot.ref.bot_id] = bot

    def disconnect(self, bot_id: str) -> OpsMeshBot:
        return self.bots.pop(bot_id)

    async def dispatch(self, event: OpsMeshEvent, bot: OpsMeshBot | None = None) -> DispatchResult:
        from opsmesh.framework.events import DispatchContext
        from opsmesh.framework.message import handle_event

        self.ensure_enabled()
        if bot is not None and self.bots.get(bot.ref.bot_id) is not bot:
            raise ValueError("OpsMeshBot is not connected to this adapter")
        return await handle_event(DispatchContext(event=event, bot=bot))
