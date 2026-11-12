"""Conversation metadata owned by the sandbox example."""

from sqlalchemy import Index
from sqlalchemy.orm import DeclarativeBase

from . import conversations


class SandboxBase(DeclarativeBase):
    """Declarative metadata for the sandbox plugin."""


class Conversation(conversations.ConversationRecord, SandboxBase):
    """A sandbox conversation independent of the SDK message tables."""

    __tablename__ = "example_sandbox_conversations"
    __table_args__ = (
        Index("ix_sandbox_owner_updated", "owner_id", "updated_at"),
    )
