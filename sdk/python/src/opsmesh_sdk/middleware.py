"""Model middleware executes at the host I/O boundary, never during replay."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass
class ModelRequest:
    operation_id: str
    run_id: UUID
    revision_id: UUID
    instructions: str | None
    input: str | list[Any]


class ModelMiddleware:
    """Lower priority enters first and exits last; callbacks must be idempotent.

    before_model may change instructions/input. after_model receives the native
    Agents SDK ModelResponse and may replace it while preserving native usage.
    External effects must use request.operation_id as their idempotency key.
    """

    async def before_model(self, request: ModelRequest) -> None:
        pass

    async def after_model(self, request: ModelRequest, response: Any) -> Any:
        return response
