"""Public backend observations, separate from durable platform lifecycle events."""

from typing import Any

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class AgentExecutionEvent(OpsMeshContract):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
