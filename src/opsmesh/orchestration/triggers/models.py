"""Trigger definitions, unique consumer revisions and durable delivery receipts."""

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FlowTrigger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flow_triggers"
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    initiated_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    configuration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orchestration_configurations.id"), index=True
    )
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("orchestration_definition_revisions.id"))
    kind: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="enabled")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    secret_ciphertext: Mapped[str | None] = mapped_column(nullable=True)


class EventSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_subscriptions"
    __table_args__ = (UniqueConstraint("trigger_id", "consumer_revision"),)
    trigger_id: Mapped[UUID] = mapped_column(ForeignKey("flow_triggers.id"))
    consumer_revision: Mapped[int] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(200), index=True)


class TriggerReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trigger_receipts"
    __table_args__ = (UniqueConstraint("trigger_id", "consumer_revision", "event_id"),)
    trigger_id: Mapped[UUID] = mapped_column(ForeignKey("flow_triggers.id"))
    consumer_revision: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str] = mapped_column(String(240))
    fingerprint: Mapped[str] = mapped_column(String(64))
    flow_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("flow_runs.id"))
    status: Mapped[str] = mapped_column(String(24))
