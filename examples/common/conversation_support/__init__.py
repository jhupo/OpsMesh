"""Reusable example support; each consumer owns its schema and sessions."""

from opsmesh.framework.plugins import PluginMetadata

from .models import ConversationRecord
from .schemas import (
    ChatReply,
    ChatRequest,
    ConversationCreate,
    ConversationView,
)
from .store import ConversationError, ConversationStore

__plugin_meta__ = PluginMetadata(
    name="Conversation example support",
    description="Shared ownership checks and native SDK history scopes",
    usage="Subclass ConversationStore with owned models and table names.",
    type="library",
)

__all__ = [
    "ConversationRecord",
    "ConversationStore",
    "ConversationError",
    "ChatReply",
    "ChatRequest",
    "ConversationCreate",
    "ConversationView",
]
