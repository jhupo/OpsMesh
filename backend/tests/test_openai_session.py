import asyncio

import pytest
from agents import Session
from agents.items import TResponseInputItem

from backend.app.agent_runtime.openai_session import OpenAISessionAdapter


class MemoryStorage:
    session_id = "workspace:team:agent"

    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    async def get_items(self, limit: int | None = None) -> list[dict[str, object]]:
        return self.items[-limit:] if limit is not None else list(self.items)

    async def add_items(self, items: list[dict[str, object]]) -> None:
        self.items.extend(items)

    async def pop_item(self) -> dict[str, object] | None:
        return self.items.pop() if self.items else None

    async def clear_session(self) -> None:
        self.items.clear()


def test_openai_session_delegates_history_operations_through_sdk_contract() -> None:
    storage = MemoryStorage()
    sdk_session: Session = OpenAISessionAdapter(storage)
    assert sdk_session.session_id == storage.session_id
    assert sdk_session.session_settings is None
    items: list[TResponseInputItem] = [
        {"role": "user", "content": "continue"},
        {"type": "function_call_output", "call_id": "call-1", "output": "done"},
    ]

    async def exercise() -> None:
        await sdk_session.add_items(items)
        assert await sdk_session.get_items() == items
        assert await sdk_session.get_items(limit=1) == items[-1:]
        assert await sdk_session.pop_item() == items[-1]
        assert storage.items == items[:1]
        await sdk_session.clear_session()
        assert storage.items == []
        assert await sdk_session.pop_item() is None

    asyncio.run(exercise())


def test_openai_session_rejects_foreign_history_without_exposing_input() -> None:
    storage = MemoryStorage()
    storage.items = [{"type": "claude_native_session", "token": "private-value"}]
    sdk_session = OpenAISessionAdapter(storage)
    with pytest.raises(ValueError) as raised:
        asyncio.run(sdk_session.get_items())
    assert str(raised.value) == "Stored session item is not a valid OpenAI input item"
    assert raised.value.__suppress_context__ is True
    assert storage.items == [{"type": "claude_native_session", "token": "private-value"}]
