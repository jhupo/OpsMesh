"""Bind reusable conversation operations to this plugin's tables."""

from conversation_support import ConversationError as ConversationError

from . import conversations
from .models import Conversation


class ChatConversations(conversations.ConversationStore):
    """Use chat-owned indexes and native SDK history tables."""

    record_type = Conversation
    sessions_table = "example_chat_sdk_sessions"
    messages_table = "example_chat_sdk_messages"
