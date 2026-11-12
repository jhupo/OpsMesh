"""Register the plugin schema and bind the shared PostgreSQL engine."""

from opsmesh import on_shutdown, on_startup

from . import database
from .sessions import ChatConversations

_conversations: ChatConversations | None = None


def get_conversations() -> ChatConversations:
    """Return the initialized conversation owner."""
    if _conversations is None:
        raise RuntimeError("Chat conversations have not started")
    return _conversations


@on_startup
async def startup() -> None:
    """Require migrated tables before accepting conversations."""
    global _conversations
    _conversations = ChatConversations(database.get_engine())


@on_shutdown
async def shutdown() -> None:
    """Forget the owner; the database plugin disposes its own engine."""
    global _conversations
    _conversations = None
