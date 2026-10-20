"""Event contracts: envelopes, subscription needs and consumption results.

Subscriptions themselves belong to ``orchestration.triggers``; this module only
declares what a package consumes and how occurrences are deduplicated. A
package may publish its own namespaced events, never forge platform facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.values import ScopeRef


@dataclass(frozen=True)
class EventEnvelope:
    """An extension notification occurrence, with no policy or dispatch behavior."""

    event_type: str
    source: str
    payload_json: str = "{}"
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.event_type or not self.source:
            raise ValueError("A notification requires type and source")
        if self.occurred_at.utcoffset() is None:
            raise ValueError("Notification timestamps must include a timezone")
        if len(self.payload_json.encode()) > 65536:
            raise ValueError("Notification payload exceeds 64 KiB")
        if not isinstance(json.loads(self.payload_json), dict):
            raise ValueError("Notification payload must be a JSON object")

    @property
    def payload(self) -> dict[str, object]:
        value: dict[str, object] = json.loads(self.payload_json)
        return value


class EventOrigin(StrEnum):
    PLATFORM = "platform"
    EXTENSION_REPORTED = "extension_reported"


class EventFilter(BaseModel):
    """A declared consumption filter; the server still enforces current scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_types: tuple[str, ...] = Field(min_length=1)
    scopes: tuple[ScopeRef, ...] = ()
    require_origin: EventOrigin | None = None


class EventSubscriptionNeed(BaseModel):
    """A package's declared need to consume events through core triggers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    export_id: str = Field(min_length=1, max_length=100)
    operation_export_id: str = Field(min_length=1, max_length=100)
    filter: EventFilter
    idempotency_key_fields: tuple[str, ...] = Field(min_length=1)


class EventConsumptionResult(BaseModel):
    """The receipt of one consumed occurrence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    duplicate: bool = False
    checkpoint: str | None = Field(default=None, max_length=512)
    operation_result: JsonValue = None


__all__ = [
    "EventConsumptionResult",
    "EventEnvelope",
    "EventFilter",
    "EventOrigin",
    "EventSubscriptionNeed",
]
