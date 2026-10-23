"""Host registration of transaction-scoped orchestration engine factories."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workflows.engines.base import WorkflowEngine


class EngineRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Callable[[Session], WorkflowEngine]] = {}

    def register(self, name: str, factory: Callable[[Session], WorkflowEngine]) -> None:
        if name in self._items:
            raise ValueError(f"Workflow engine already registered: {name}")
        self._items[name] = factory

    def get(self, name: str, session: Session) -> WorkflowEngine:
        if name not in self._items:
            raise InvalidArgumentError("The requested workflow engine is not configured")
        return self._items[name](session)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))
