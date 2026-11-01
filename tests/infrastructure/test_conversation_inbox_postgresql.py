"""Real PostgreSQL inbox contention and recovery using a disposable local cluster."""

import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.bots.conversations import QueuedConversationMessage
from opsmesh.modules.bots.internal.conversations.inbox import PersistentConversationInboxProvider
from tests.infrastructure.test_postgresql import postgres as postgres
from tests.infrastructure.test_postgresql import postgres_url as postgres_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPSMESH_TEST_POSTGRES_INTEGRATION") != "1",
        reason="Explicit temporary PostgreSQL integration",
    ),
]


async def test_inbox_deduplication_claims_isolation_and_recovery_across_connections(postgres_url):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    providers = [PersistentConversationInboxProvider(db) for db in databases]
    try:
        await databases[0].initialize()
        await databases[1].verify_schema()
        first, second = [provider.open("same-project") for provider in providers]
        # Opaque IDs may be long and incompressible; indexed keys have bounded hashes.
        message = QueuedConversationMessage(
            event_id=secrets.token_hex(1024),
            bot_id=secrets.token_hex(1024),
            channel_id=secrets.token_hex(1024),
            user_id="member",
            message_id="provider-id",
            text="Question",
            occurred_at=datetime.now(UTC),
            to_me=True,
        )
        inserted = await asyncio.gather(*(inbox.enqueue(message) for inbox in [first, second] * 4))
        assert sum(inserted) == 1
        await first.set_moderation(message.event_id, {"status": "ok", "action": "allow"}, True)
        claims = await asyncio.gather(
            *(
                inbox.claim_batch(message.bot_id, message.channel_id)
                for inbox in [first, second] * 4
            )
        )
        claimed = [batch for batch in claims if batch is not None]
        assert len(claimed) == 1
        batch = claimed[0]
        await first.record_run(batch.batch_id, "original-run")
        await first.set_value("capture:one", {"run_id": "original-run"})
        assert (await second.pending_batches())[0].run_id == "original-run"
        assert await second.get_value("capture:one") == {"run_id": "original-run"}
        isolated = providers[1].open("another-project")
        assert await isolated.pending_moderation() == ()
        assert await isolated.pending_batches() == ()
        assert await isolated.get_value("capture:one") is None
        await second.complete_batch(batch.batch_id, run_id="original-run", state="observed")
        assert await first.purge_before(datetime.now(UTC) + timedelta(days=1)) == 1
        assert await first.pending_batches() == ()
    finally:
        await asyncio.gather(*(db.close() for db in databases))
