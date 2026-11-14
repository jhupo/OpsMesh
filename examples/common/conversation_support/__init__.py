"""Reusable example support; each consumer owns its schema and sessions."""

from opsmesh.plugins import PluginMetadata, require

channels = require("channels")

from .models import ConversationRecord  # noqa: E402
from .schemas import ConversationView  # noqa: E402
from .store import ConversationError, ConversationStore  # noqa: E402

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
    "ConversationView",
]
