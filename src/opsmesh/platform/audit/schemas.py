"""Extension audit input, sanitized event/receipt, and cursor contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opsmesh.platform.audit.models import MAX_ACTION_LENGTH, MAX_SUMMARY_LENGTH, MAX_TARGET_LENGTH
from opsmesh.shared.serialization import canonical_json

MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_DETAILS_FIELDS = 64
MAX_DETAILS_BYTES = 16_384
MAX_EVIDENCE_REFS = 20

# Audit payloads carry business facts, never credentials: a key that names a
# secret is rejected at the boundary instead of being persisted and screened on
# read.
FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "session_id",
        "token",
    }
)


class ExtensionAuditAppend(BaseModel):
    """One extension-reported fact; actor and scope come from the verified call."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=MAX_ACTION_LENGTH)
    target_type: str = Field(min_length=1, max_length=MAX_TARGET_LENGTH)
    target_id: str = Field(min_length=1, max_length=MAX_TARGET_LENGTH)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    details: dict[str, object] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_REFS)
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)

    @field_validator("details")
    @classmethod
    def validate_details(cls, details: dict[str, object]) -> dict[str, object]:
        if len(details) > MAX_DETAILS_FIELDS:
            raise ValueError(f"An audit detail payload carries at most {MAX_DETAILS_FIELDS} fields")
        if len(canonical_json(details).encode()) > MAX_DETAILS_BYTES:
            raise ValueError(f"An audit detail payload carries at most {MAX_DETAILS_BYTES} bytes")
        _reject_secret_keys(details)
        return details

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, refs: list[str]) -> list[str]:
        for ref in refs:
            kind, separator, identifier = ref.partition(":")
            if not separator or not kind.isidentifier() or not identifier:
                raise ValueError("An evidence reference has the form kind:identifier")
            if len(kind) > 32 or len(identifier) > 160:
                raise ValueError("An evidence reference is too long")
        return refs


class AuditEventView(BaseModel):
    """One sanitized audit fact with its database-assigned occurrence sequence."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    sequence: int
    occurred_at: datetime
    recorded_at: datetime
    category: str
    severity: str
    schema_version: int
    operation_id: str | None
    correlation_id: str | None
    causation_id: UUID | None
    trace_id: str | None
    actor_type: str
    actor_id: str | None
    configuration_id: UUID | None
    installation_id: UUID | None
    instance_id: UUID | None
    invocation_id: UUID | None
    package_key: str | None
    configuration_revision: str | None
    action: str
    target_type: str
    target_id: str
    summary: str
    details: dict[str, object]
    evidence_refs: list[str]
    source: str


class AuditEventPage(BaseModel):
    """One cursor page of audit events ordered by occurrence sequence."""

    model_config = ConfigDict(extra="forbid")

    events: list[AuditEventView]
    next_after_sequence: int | None


class AuditAppendView(BaseModel):
    """The receipt of one extension append; a replayed key reports no new fact."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    replayed: bool


def _reject_secret_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN_DETAIL_KEYS:
                raise ValueError(f"The audit detail field {key!r} may not carry credentials")
            _reject_secret_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_keys(item)
