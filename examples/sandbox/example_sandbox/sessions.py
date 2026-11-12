"""Bind native history and workspace cleanup to owned conversations."""

from conversation_support import ConversationError as ConversationError
from sqlalchemy.ext.asyncio import AsyncEngine

from . import conversations
from .models import Conversation
from .workspace import SandboxWorkspaces


class SandboxConversations(conversations.ConversationStore):
    """Use sandbox-owned indexes and retain files between model turns."""

    record_type = Conversation
    sessions_table = "example_sandbox_sdk_sessions"
    messages_table = "example_sandbox_sdk_messages"

    def __init__(
        self, engine: AsyncEngine, workspaces: SandboxWorkspaces
    ) -> None:
        """Borrow the database engine and plugin-owned workspace manager."""
        super().__init__(engine)
        self.workspaces = workspaces

    async def remove_assets(self, session_id: str) -> None:
        """Delete files and persisted SDK sandbox state before the index."""
        await self.workspaces.reset(session_id)

    async def reset_workspace(self, owner_id: str, session_id: str) -> None:
        """Reset files explicitly without deleting conversation history."""
        async with self.hold(owner_id, session_id):
            await self.workspaces.reset(session_id)
