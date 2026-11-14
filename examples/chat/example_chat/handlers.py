"""Expose shared conversation commands and ordinary chat messages."""

from conversation_support.entrance import ConversationEntrance

from opsmesh import on_shutdown

from .conversation import answer
from .lifecycle import get_conversations

entrance = ConversationEntrance(get_conversations, answer)
entrance.bind()


@on_shutdown
def forget_selections() -> None:
    """Release window mappings after admitted conversations drain."""
    entrance.forget_selections()
