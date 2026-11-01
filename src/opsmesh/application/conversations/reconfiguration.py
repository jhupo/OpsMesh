"""Prevent reconfiguration while a local conversation consumer still owns work."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.coordination.process_lock import LocalProcessLock
from opsmesh.modules.bots.conversations import ConversationInbox


@asynccontextmanager
async def reconfiguration_scope(
    data_directory: Path, inbox: ConversationInbox
) -> AsyncIterator[None]:
    lock = LocalProcessLock(data_directory / "consumer.lock")
    try:
        lock.acquire()
    except RuntimeError:
        raise OpsMeshError(
            "CONVERSATION_BINDING_BUSY", "Stop the conversation consumer before applying changes"
        ) from None
    try:
        if await inbox.pending_batches():
            raise OpsMeshError(
                "CONVERSATION_BINDING_BUSY",
                "Drain or recover active conversation batches before applying changes",
            )
        if await inbox.list_values("conversation-capture:", limit=1):
            raise OpsMeshError(
                "CONVERSATION_BINDING_BUSY",
                "Finish pending knowledge captures before applying changes",
            )
        yield
    finally:
        lock.release()
