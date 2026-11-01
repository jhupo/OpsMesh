"""Shared test resources use the same database lifecycle as platform bootstrap."""

import pytest_asyncio

from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.bots.internal.conversations.inbox import (
    PersistentConversationInbox,
    PersistentConversationInboxProvider,
)


@pytest_asyncio.fixture
async def inbox_factory():
    databases = []

    async def create(path, *, namespace="test", clock=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        database = AsyncDatabase(f"sqlite+aiosqlite:///{path.as_posix()}")
        databases.append(database)
        provider = PersistentConversationInboxProvider(database)
        await database.initialize()
        return PersistentConversationInbox(provider.storage, namespace, clock=clock)

    try:
        yield create
    finally:
        for database in databases:
            await database.close()
