"""Conversation metadata owned by the chat example."""

from sqlalchemy import Index
from sqlalchemy.orm import DeclarativeBase

from . import conversations


class ChatBase(DeclarativeBase):
    """Declarative metadata for the chat plugin."""


class Conversation(conversations.ConversationRecord, ChatBase):
    """A chat conversation independent of the SDK message tables."""

    __tablename__ = "example_chat_conversations"
    __table_args__ = (Index("ix_chat_owner_updated", "owner_id", "updated_at"),)
