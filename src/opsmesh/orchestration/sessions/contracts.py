"""Internal session contracts: references, history operations and the run guard.

These values are the serializable core of a model session operation: what
a caller references, which native history method runs, and what its
confirmed result was. The HTTP contract lives in schemas.py; the receipt
row persists exactly the payload this module defines, so a replayed
operation returns the recorded result instead of touching the native
history again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

HistoryMethod = Literal["get_items", "add_items", "pop_item", "clear_session"]


@dataclass(frozen=True)
class SessionReference:
    """One serializable reference to a model session."""

    session_id: UUID


@dataclass(frozen=True)
class HistoryOperation:
    """One native history operation of one session.

    Exactly the fields its method uses are carried: items only for an
    append, a limit only for a read. The operation payload derived from
    it is the idempotency fingerprint, so the same logical operation
    always produces the same receipt.
    """

    method: HistoryMethod
    items: tuple[dict[str, Any], ...] = field(default=())
    limit: int | None = None


def operation_payload(operation: HistoryOperation) -> dict[str, Any]:
    """The canonical payload of one operation, reduced to its method's fields."""
    payload: dict[str, Any] = {"method": operation.method}
    if operation.method == "add_items":
        payload["items"] = list(operation.items)
    if operation.method == "get_items" and operation.limit is not None:
        payload["limit"] = operation.limit
    return payload


@dataclass(frozen=True)
class HistoryMutationResult:
    """The confirmed result of one history mutation.

    Each method sets exactly one field: an append reports how many items
    it stored, a pop reports the removed item (None when the history was
    empty), a clear reports that it ran. The same shape is persisted in
    the receipt and returned again on replay.
    """

    appended: int | None = None
    item: dict[str, Any] | None = None
    cleared: bool = False


@runtime_checkable
class SessionRunActivity(Protocol):
    """Whether one session currently has runs that own its history.

    The runs table belongs to execution.runs, which sessions never
    imports: bootstrap assembles the real query and injects it. The
    default refuses, so an unassembled application fails closed instead
    of mutating history a running agent may be writing.
    """

    def has_active_runs(self, session: Session, *, session_id: UUID) -> bool:
        """Report whether any non-terminal run of the session is still active."""
        ...


class RejectingSessionRunActivity:
    """The default guard: every session is treated as having active runs."""

    def has_active_runs(self, session: Session, *, session_id: UUID) -> bool:
        _ = session, session_id
        return True


class SessionActivityRequest(BaseModel):
    """Only references and native values cross the Workflow boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: UUID
    session_id: UUID
    run_id: UUID
    method: HistoryMethod
    items: list[dict[str, Any]] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)


class SessionWriter(Protocol):
    def authorize_writer(
        self, session: Session, *, session_id: UUID, run_id: UUID, principal_id: UUID
    ) -> None: ...


@runtime_checkable
class SessionRetention(Protocol):
    def has_retained_runs(self, session: Session, *, session_id: UUID) -> bool: ...
