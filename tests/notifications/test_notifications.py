"""Durable fanout, identity boundaries and conservative delivery outcomes."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from opsmesh.framework.adapters import BotRef, ReplyTarget
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.accounts.internal.service import LocalAccountService
from opsmesh.modules.audit.internal.journal import PersistentAuditJournal
from opsmesh.modules.bots.api import DeliveryReceipt
from opsmesh.modules.notifications.api import NotificationSubscription
from opsmesh.modules.notifications.internal.service import NotificationService
from opsmesh.modules.notifications.management import NotificationManagement


class RecordingDelivery:
    def __init__(self, authorize):
        self.authorize = authorize
        self.calls = []
        self.failure = None

    async def send(self, target, message, *, idempotency_key, actor_id):
        await self.authorize(actor_id, "bots.send", target.bot.bot_id)
        self.calls.append((idempotency_key, actor_id))
        if self.failure:
            raise self.failure
        return DeliveryReceipt(delivery_id=idempotency_key, message_id="confirmed")


@pytest.fixture
async def platform(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'notifications.db').as_posix()}")
    accounts = LocalAccountService(database)
    audit = PersistentAuditJournal(database)

    async def authorize(actor_id, action, resource):
        await accounts.authorize(await accounts.get_principal(actor_id), action, resource)

    delivery = RecordingDelivery(authorize)
    first = NotificationService(database, accounts, audit, delivery, authorize)
    second = NotificationService(database, accounts, audit, delivery, authorize)
    await database.initialize()
    await accounts.create_role(
        AccessRole(name="sender", grants=(PermissionGrant(action="bots.send", resource="support"),))
    )
    users = {
        name: await accounts.create_user(
            name,
            "test-password-123",
            roles=("sender",) if name == "owner" else (),
            is_admin=name == "admin",
        )
        for name in ("owner", "other", "admin")
    }
    target = ReplyTarget(bot=BotRef(adapter_id="qq", bot_id="support"), channel_id="c2c:1")
    try:
        yield first, second, delivery, users, target
    finally:
        await database.close()


async def test_owner_and_target_authorization_are_checked_in_service(platform):
    first, _second, delivery, users, target = platform
    management = NotificationManagement(first)
    assert not hasattr(management, "observe") and not hasattr(management, "tick")
    subscription = NotificationSubscription(
        id="updates", owner_id=users["owner"].id, event_types=("runtime.run.failed",), target=target
    )
    with pytest.raises(OpsMeshError):
        await management.save_subscription(subscription, actor_id=users["other"].id)
    saved = await management.save_subscription(subscription, actor_id=users["owner"].id)
    assert saved.revision == 1
    denied = subscription.model_copy(
        update={"id": "unauthorized-target", "owner_id": users["other"].id}
    )
    with pytest.raises(OpsMeshError):
        await management.save_subscription(denied, actor_id=users["admin"].id)
    await first.observe(
        event_id="e1",
        event_type="runtime.run.failed",
        owner_id=users["owner"].id,
        resource_id="run",
        status="failed",
    )
    with pytest.raises(OpsMeshError):
        await management.list_records(actor_id=users["other"].id, owner_id=users["owner"].id)
    assert await management.list_records(actor_id=users["other"].id) == []
    await first.accounts.update_user(users["owner"].id, active=False)
    with pytest.raises(OpsMeshError):
        await management.list_records(actor_id=users["owner"].id)
    await first.tick()
    assert delivery.calls == []


async def test_duplicate_observations_and_two_workers_deliver_once(platform):
    first, second, delivery, users, target = platform
    owner = users["owner"].id
    # A user subscription named inbox must not collide with the platform inbox record.
    await first.save_subscription(
        NotificationSubscription(
            id="inbox", owner_id=owner, event_types=("runtime.run.failed",), target=target
        ),
        actor_id=owner,
    )
    event = dict(
        event_id="e1",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
    )
    await asyncio.gather(first.observe(**event), second.observe(**event), first.observe(**event))
    records = await first.list_records(actor_id=owner)
    assert len(records) == 2
    await asyncio.gather(first.tick(), second.tick())
    await first.tick()
    assert len(delivery.calls) == 1
    assert all(item.status == "sent" for item in await second.list_records(actor_id=owner))
    inbox = next(item for item in await first.list_records(actor_id=owner) if item.target is None)
    await first.mark_read(inbox.id, actor_id=owner, expected_revision=inbox.revision)
    with pytest.raises(OpsMeshError) as stale:
        await second.mark_read(inbox.id, actor_id=owner, expected_revision=inbox.revision)
    assert stale.value.code == "CONFLICT"


async def test_uncertain_or_expired_delivery_is_never_automatically_retried(platform):
    first, second, delivery, users, target = platform
    owner = users["owner"].id
    delivery.failure = OpsMeshError("DELIVERY_UNKNOWN", "Peer may have accepted the request")
    await first.observe(
        event_id="e1",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
        default_target=target,
    )
    await first.tick()
    item = (await first.list_records(actor_id=owner))[0]
    assert item.status == "unknown"
    for _ in range(2):
        await second.tick()
    assert len(delivery.calls) == 1
    with pytest.raises(OpsMeshError):
        await first.retry(item.id, actor_id=owner, expected_revision=item.revision)
    await first.observe(
        event_id="e2",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
        default_target=target,
    )
    pending = next(
        item for item in await first.list_records(actor_id=owner) if item.status == "pending"
    )
    row = await first.records.get(pending.id)
    await first.records.update(
        pending.id,
        {
            **row.data,
            "status": "delivering",
            "lease_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
        row.revision,
    )
    await second.tick()
    assert (await first._get(pending.id)).status == "unknown"
    assert len(delivery.calls) == 1


async def test_confirmed_failure_needs_explicit_retry_and_keeps_delivery_identity(platform):
    first, second, delivery, users, target = platform
    owner = users["owner"].id
    delivery.failure = OpsMeshError("BOT_UNAVAILABLE", "No connected transport", retryable=True)
    await first.observe(
        event_id="e3",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
        default_target=target,
    )
    await first.tick()
    item = (await first.list_records(actor_id=owner))[0]
    assert item.status == "failed"
    await second.tick()
    assert len(delivery.calls) == 1
    with pytest.raises(OpsMeshError):
        await first.retry(item.id, actor_id=users["other"].id, expected_revision=item.revision)
    retried = await first.retry(item.id, actor_id=owner, expected_revision=item.revision)
    assert retried.status == "pending"
    delivery.failure = None
    await second.tick()
    assert len(delivery.calls) == 2
    assert delivery.calls[0] == delivery.calls[1]
    assert (await first._get(item.id)).status == "sent"


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [(None, "sent"), ("BOT_UNAVAILABLE", "failed"), ("DELIVERY_UNKNOWN", "unknown")],
)
async def test_reading_inbox_during_send_preserves_delivery_result(
    platform, monkeypatch, failure, expected_status
):
    first, second, delivery, users, target = platform
    owner = users["owner"].id
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = delivery.send
    if failure:
        delivery.failure = OpsMeshError(failure, "Controlled delivery result")

    async def held_send(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_send(*args, **kwargs)

    monkeypatch.setattr(delivery, "send", held_send)
    await first.observe(
        event_id="read-during-send",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
        default_target=target,
    )
    task = asyncio.create_task(first.tick())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        item = (await second.list_records(actor_id=owner))[0]
        await second.mark_read(item.id, actor_id=owner, expected_revision=item.revision)
    finally:
        release.set()
        await task
    result = (await second.list_records(actor_id=owner))[0]
    assert result.read and result.status == expected_status and result.lease_until is None
    assert result.error_code == failure
    assert bool(result.delivery_id) == (failure is None)
    await second.tick()
    assert len(delivery.calls) == 1


async def test_late_receipt_resolves_expired_attempt_without_resending(platform, monkeypatch):
    first, second, delivery, users, target = platform
    owner = users["owner"].id
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = delivery.send

    async def held_send(*args, **kwargs):
        receipt = await original_send(*args, **kwargs)
        entered.set()
        await release.wait()
        return receipt

    monkeypatch.setattr(delivery, "send", held_send)
    await first.observe(
        event_id="late-receipt",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
        default_target=target,
    )
    task = asyncio.create_task(first.tick())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        item = (await second.list_records(actor_id=owner))[0]
        row = await first.records.get(item.id)
        await first.records.update(
            item.id,
            {
                **row.data,
                "lease_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
            row.revision,
        )
        await second.tick()
        expired = await first._get(item.id)
        assert expired.status == "unknown"
        await second.mark_read(item.id, actor_id=owner, expected_revision=expired.revision)
    finally:
        release.set()
        await task
    result = await first._get(item.id)
    assert result.status == "sent" and result.read and result.error_code is None
    assert len(delivery.calls) == 1


async def test_old_attempt_cannot_complete_a_new_delivery(platform, monkeypatch):
    first, second, delivery, users, target = platform
    owner = users["owner"].id
    await first.observe(
        event_id="attempt-fence",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
        default_target=target,
    )
    original_send = delivery.send
    claims = []

    async def record_claim(*args, **kwargs):
        item = (await first.list_records(actor_id=owner))[0]
        claims.append(await first.records.get(item.id))
        if len(claims) == 1:
            raise OpsMeshError("BOT_UNAVAILABLE", "Connection unavailable", retryable=True)
        # Completion from a previous attempt must not overwrite this new claim.
        assert not await first._delivery_state.complete(
            claims[0], {"status": "sent", "delivery_id": "stale-receipt"}
        )
        return await original_send(*args, **kwargs)

    monkeypatch.setattr(delivery, "send", record_claim)
    await first.tick()
    failed = (await first.list_records(actor_id=owner))[0]
    await first.retry(failed.id, actor_id=owner, expected_revision=failed.revision)
    await second.tick()
    result = await first._get(failed.id)
    assert result.status == "sent" and result.attempts == 2
    assert result.delivery_id != "stale-receipt"


@pytest.mark.parametrize("operation", ["mark_read", "retry"])
async def test_future_revision_cannot_overwrite_delivery_during_authorization(
    platform, monkeypatch, operation
):
    first, second, delivery, users, target = platform
    owner = users["owner"].id
    await first.observe(
        event_id="future-revision",
        event_type="runtime.run.failed",
        owner_id=owner,
        resource_id="run",
        status="failed",
        default_target=target,
    )
    if operation == "retry":
        delivery.failure = OpsMeshError("BOT_UNAVAILABLE", "Disconnected", retryable=True)
        await first.tick()
        delivery.failure = None
    item = (await second.list_records(actor_id=owner))[0]
    entered, release = asyncio.Event(), asyncio.Event()
    original_owner = first._owner

    async def held_owner(*args, **kwargs):
        await original_owner(*args, **kwargs)
        entered.set()
        await release.wait()

    monkeypatch.setattr(first, "_owner", held_owner)
    future_revision = item.revision + (3 if operation == "retry" else 2)
    request = asyncio.create_task(
        getattr(first, operation)(item.id, actor_id=owner, expected_revision=future_revision)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        if operation == "retry":
            await second.retry(item.id, actor_id=owner, expected_revision=item.revision)
        await second.tick()
        sent = await second._get(item.id)
        assert sent.status == "sent" and sent.revision == future_revision
    finally:
        release.set()
    with pytest.raises(OpsMeshError) as conflict:
        await request
    assert conflict.value.code == "CONFLICT"
    final = await second._get(item.id)
    assert final.status == "sent" and final.delivery_id == sent.delivery_id and not final.read
