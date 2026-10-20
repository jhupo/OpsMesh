"""Source owners provide authenticated facts, never a client-selected platform event."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.triggers.schemas import SourceEventWork


@dataclass(frozen=True)
class SourceEvent:
    event_type: str
    payload: dict[str, Any]
    configuration_id: UUID | None
    instance_id: UUID | None = None
    parent_flow_id: UUID | None = None


class SourceEvents(Protocol):
    def read(
        self, session: Session, *, actor: CurrentUser, reference: SourceEventWork
    ) -> SourceEvent: ...
