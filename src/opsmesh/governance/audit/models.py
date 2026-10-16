"""Audit facts: append-only events, idempotency receipts, chain head, verifications."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.governance.audit.hashing import GENESIS_HASH, canonical_payload
from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MAX_SUMMARY_LENGTH = 2000
MAX_ACTION_LENGTH = 200
MAX_TARGET_LENGTH = 200


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """One appended audit fact; rows are never updated or deleted."""

    __tablename__ = "audit_events"

    sequence: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    workspace_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    installation_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    instance_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    invocation_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    package_key: Mapped[str | None] = mapped_column(String(100))
    configuration_revision: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(MAX_ACTION_LENGTH), nullable=False)
    target_type: Mapped[str] = mapped_column(String(MAX_TARGET_LENGTH), nullable=False)
    target_id: Mapped[str] = mapped_column(String(MAX_TARGET_LENGTH), nullable=False)
    summary: Mapped[str] = mapped_column(String(MAX_SUMMARY_LENGTH), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    @property
    def chain_payload(self) -> dict[str, object]:
        """The exact field mapping the event digest covers."""
        return {
            "sequence": self.sequence,
            "occurred_at": self.occurred_at.isoformat(),
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "workspace_id": str(self.workspace_id) if self.workspace_id else None,
            "project_id": str(self.project_id) if self.project_id else None,
            "installation_id": str(self.installation_id) if self.installation_id else None,
            "instance_id": str(self.instance_id) if self.instance_id else None,
            "invocation_id": str(self.invocation_id) if self.invocation_id else None,
            "package_key": self.package_key,
            "configuration_revision": self.configuration_revision,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "summary": self.summary,
            "details": self.details,
            "evidence_refs": list(self.evidence_refs),
            "source": self.source,
        }

    def payload_bytes(self) -> bytes:
        return canonical_payload(self.chain_payload)


class AuditChainHead(Base):
    """The singleton chain head; its row lock serializes concurrent appends."""

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=GENESIS_HASH
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AuditAppendReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The idempotent receipt of one declared audit append."""

    __tablename__ = "audit_append_receipts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_scope",
            "idempotency_key",
            name="uq_audit_receipts_scope_key",
        ),
    )

    idempotency_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)


class AuditVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One chain verification run and its durable result."""

    __tablename__ = "audit_verifications"

    requested_by_user_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    from_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    to_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checked_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    first_broken_sequence: Mapped[int | None] = mapped_column(BigInteger)
    failure_reason: Mapped[str | None] = mapped_column(String(500))
