"""Real PostgreSQL row locks preserve inbox reads and fence delivery attempts."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.framework.adapters import BotRef, ReplyTarget
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.accounts.internal.service import LocalAccountService
from opsmesh.modules.audit.internal.journal import PersistentAuditJournal
from opsmesh.modules.bots.api import DeliveryReceipt
from opsmesh.modules.notifications.internal.service import NotificationService
from tests.infrastructure.test_postgresql import postgres as postgres
from tests.infrastructure.test_postgresql import postgres_url as postgres_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPSMESH_TEST_POSTGRES_INTEGRATION") != "1",
        reason="Explicit temporary PostgreSQL integration",
    ),
]


async def test_postgres_notification_receipt_merges_concurrent_read(postgres_url):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    entered, release = asyncio.Event(), asyncio.Event()
    sends = []

    class HeldDelivery:
        async def send(self, target, message, *, idempotency_key, actor_id):
            sends.append(idempotency_key)
            entered.set()
            await release.wait()
            return DeliveryReceipt(delivery_id="confirmed", message_id="message")

    services = []
    for database in databases:
        accounts = LocalAccountService(database)
        audit = PersistentAuditJournal(database)

        async def authorize(actor_id, action, resource):
            raise AssertionError("There are no subscriptions or permission mutations")

        services.append(NotificationService(database, accounts, audit, HeldDelivery(), authorize))
    first, second = services
    try:
        await databases[0].initialize()
        await databases[1].verify_schema()
        owner = await first.accounts.create_user("owner", "offline-review-password", is_admin=True)
        await first.observe(
            event_id="notification",
            event_type="runtime.run.failed",
            owner_id=owner.id,
            resource_id="run",
            status="failed",
            default_target=ReplyTarget(
                bot=BotRef(adapter_id="qq", bot_id="support"), channel_id="c2c:1"
            ),
        )
        task = asyncio.create_task(first.tick())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            item = (await second.list_records(actor_id=owner.id))[0]
            claim = await second.records.get(item.id)
            await second.mark_read(item.id, actor_id=owner.id, expected_revision=item.revision)
            # Another process observes an expired lease before the confirmed receipt arrives.
            current = await second.records.get(item.id)
            await second.records.update(
                item.id,
                {
                    **current.data,
                    "lease_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                },
                current.revision,
            )
            await second.tick()
            assert (await second._get(item.id)).status == "unknown"
        finally:
            release.set()
            await task
        result = await second._get(item.id)
        assert result.read and result.status == "sent" and result.delivery_id == "confirmed"
        assert result.error_code is None and len(sends) == 1
        # Even the same attempt cannot change an already committed terminal result.
        assert not await first._delivery_state.complete(claim, {"status": "failed"})
        assert (await first._get(item.id)).status == "sent"
    finally:
        await asyncio.gather(*(database.close() for database in databases))
