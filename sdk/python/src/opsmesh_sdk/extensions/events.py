"""Event contracts: envelopes, subscription needs and consumption results.

Subscriptions themselves belong to ``orchestration.triggers``; this module only
declares what a package consumes and how occurrences are deduplicated. A
package may publish its own namespaced events, never forge platform facts.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.values import ScopeRef


class EventOrigin(StrEnum):
    PLATFORM = "platform"
    EXTENSION_REPORTED = "extension_reported"


class EventEnvelope(BaseModel):
    """The wire envelope of one event occurrence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=128)
    source_revision: str | None = Field(default=None, max_length=100)
    event_id: str = Field(min_length=1, max_length=128)
    event_version: str = Field(min_length=1, max_length=50)
    scope: ScopeRef = Field(default_factory=ScopeRef)
    origin: EventOrigin = EventOrigin.PLATFORM
    causal_refs: tuple[str, ...] = ()
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    content_ref: str | None = Field(default=None, max_length=512)

    def deduplication_key(self) -> str:
        """Consumers deduplicate on subscription, event and consumer revision."""
        return f"{self.source_type}:{self.event_id}:{self.event_version}"


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
