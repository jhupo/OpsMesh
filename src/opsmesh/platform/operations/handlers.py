"""Explicit, transactional owner commands; no shell or host updater."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.shared.errors import ConflictError


@dataclass(frozen=True)
class OperationPage:
    processed: int = 0
    next_cursor: str | None = None
    details: dict[str, object] | None = None


OperationHandler = Callable[
    [Session, dict[str, object], str | None, int, CurrentUser, UUID], OperationPage
]
OperationHandlers = dict[tuple[str, str], OperationHandler]


def resolve(handlers: OperationHandlers, *, owner: str, command: str) -> OperationHandler:
    handler = handlers.get((owner, command))
    if handler is None:
        raise ConflictError("The command owner is unavailable", code="owner_unavailable")
    return handler
