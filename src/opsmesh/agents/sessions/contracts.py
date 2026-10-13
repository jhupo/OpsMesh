"""Durable Session operation request; contains identity and SDK input items only."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.agents.execution.providers.openai.contracts import AgentSessionReference


class SessionOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: AgentSessionReference
    method: Literal["get_items", "add_items", "pop_item", "clear_session"]
    # OpenAI's input TypedDict uses Iterable content. Validating that union
    # introduces lazy Pydantic iterators, which native SQLAlchemySession cannot
    # JSON serialize. Activities exchange the SDK's JSON item representation.
    items: list[dict[str, object]] = Field(default_factory=list)
    limit: int | None = None
