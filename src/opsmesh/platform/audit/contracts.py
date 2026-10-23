"""Audit attribution and immutable append input; no event dispatch or authorization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session


class AuditSubject(Protocol):
    @property
    def user_id(self) -> UUID: ...


@dataclass(frozen=True)
class AuditActor:
    """Verified attribution supplied by the trusted operation boundary."""

    actor_type: Literal["user", "platform"]
    actor_id: str | None = None

    @classmethod
    def from_user(cls, user: AuditSubject) -> AuditActor:
        return cls(actor_type="user", actor_id=str(user.user_id))


@dataclass(frozen=True)
class AuditRecord:
    """An immutable audit append input. Payload reads return detached JSON values.

    This value conveys attribution, never authority. The caller must verify
    identity and resource grants before constructing this record.
    """

    category: str
    action: str
    actor: AuditActor
    source: str
    target_type: str | None = None
    target_id: str | None = None
    summary: str = ""
    payload_json: str = "{}"
    evidence_refs: tuple[str, ...] = ()
    severity: str = "info"
    event_id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    operation_id: str | None = None
    correlation_id: str | None = None
    causation_id: UUID | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if not self.category or not self.action or self.schema_version < 1:
            raise ValueError(
                "An audit record requires category, action and a positive schema version"
            )
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Audit timestamps must include a timezone")
        if len(self.payload_json.encode("utf-8")) > 65536:
            raise ValueError("Audit payload exceeds 64 KiB")
        payload = json.loads(self.payload_json, parse_constant=_invalid_constant)
        if not isinstance(payload, dict):
            raise ValueError("Audit payload must be a JSON object")

    @property
    def payload(self) -> dict[str, object]:
        value: dict[str, object] = json.loads(self.payload_json)
        return value


def _invalid_constant(value: str) -> object:
    raise ValueError(f"Non-finite audit number: {value}")


class AuditWriter(Protocol):
    """Append in the supplied transaction; never commit or perform network I/O."""

    def __call__(self, session: Session, record: AuditRecord) -> object: ...


def record_audit(session: Session, record: AuditRecord) -> None:
    """Call the audit writer explicitly injected into this transaction's factory."""
    writer = session.info.get("audit_writer")
    if writer is None:
        raise RuntimeError("The audit writer is not assembled")
    cast(AuditWriter, writer)(session, record)
