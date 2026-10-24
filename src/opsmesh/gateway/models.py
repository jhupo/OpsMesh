from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opsmesh.infra.database import Base, Record


class ChannelConfiguration(Record, Base):
    __tablename__ = "channels"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    adapter: Mapped[str] = mapped_column(String(100))
    plugin_id: Mapped[str | None] = mapped_column(ForeignKey("plugins.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent_instances.id"))
    encrypted_config: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1)
    worker_id: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), primary_key=True)
    external_user_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class InboundMessage(Record, Base):
    __tablename__ = "inbox"
    __table_args__ = (UniqueConstraint("channel_id", "external_message_id"),)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    external_message_id: Mapped[str] = mapped_column(String(200))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    reply_sequence: Mapped[int] = mapped_column(default=0)


class OutboundDelivery(Record, Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("task_id", "deduplication_key"),)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    deduplication_key: Mapped[str] = mapped_column(String(100))
    encrypted_message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    encrypted_receipt: Mapped[str | None] = mapped_column(Text)


class ChannelEventRecord(Record, Base):
    __tablename__ = "channel_events"
    __table_args__ = (UniqueConstraint("channel_id", "external_event_id"),)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    external_event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String(24), default="recorded")
    error_code: Mapped[str | None] = mapped_column(String(100))
    encrypted_event: Mapped[str] = mapped_column(Text)


class ChannelOperation(Record, Base):
    __tablename__ = "channel_operations"
    __table_args__ = (UniqueConstraint("channel_id", "idempotency_key"),)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    encrypted_parameters: Mapped[str] = mapped_column(Text)
    encrypted_result: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
