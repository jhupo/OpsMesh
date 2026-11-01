"""Translate portable model history into the SDK session contract."""

from typing import Any, cast

from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings

from opsmesh.framework.agents.resources import ModelHistorySession


class OpenAIHistorySession:
    session_settings: SessionSettings | None = None

    def __init__(self, session_id: str, history: ModelHistorySession) -> None:
        self.session_id = session_id
        self.history = history

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return cast(list[TResponseInputItem], await self.history.get_items(limit))

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self.history.add_items(cast(list[dict[str, Any]], items))

    async def pop_item(self) -> TResponseInputItem | None:
        return cast(TResponseInputItem | None, await self.history.pop_item())

    async def clear_session(self) -> None:
        await self.history.clear_session()

    async def _atomic_replace_items(self, items: list[TResponseInputItem]) -> bool:
        return await self.history.replace_items(cast(list[dict[str, Any]], items))
