from functools import cache
from typing import cast

from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings
from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

from backend.app.agents.execution.contracts import AgentRuntimeSession


class OpenAISessionAdapter:
    """Translate product-owned storage items at the OpenAI SDK boundary."""

    session_settings: SessionSettings | None = None

    def __init__(self, storage: AgentRuntimeSession) -> None:
        self._storage = storage
        self.session_id = storage.session_id

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return [_input_item(item) for item in await self._storage.get_items(limit)]

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self._storage.add_items([dict(item) for item in items])

    async def pop_item(self) -> TResponseInputItem | None:
        item = await self._storage.pop_item()
        return _input_item(item) if item is not None else None

    async def clear_session(self) -> None:
        await self._storage.clear_session()


@cache
def _input_item_adapter() -> TypeAdapter[TResponseInputItem]:
    return TypeAdapter(TResponseInputItem)


def _input_item(value: dict[str, object]) -> TResponseInputItem:
    try:
        adapter = _input_item_adapter()
        validated = adapter.validate_python(value, strict=True)
        # Iterable fields can validate to lazy ValidatorIterator objects. SDK RunState
        # deep-copies history, so fully materialize validated items at this boundary.
        return cast(
            TResponseInputItem, adapter.dump_python(validated, mode="json", warnings="error")
        )
    except (ValidationError, PydanticSerializationError):
        # Provider histories may contain sensitive input; never expose schema error input values.
        raise ValueError("Stored session item is not a valid OpenAI input item") from None
