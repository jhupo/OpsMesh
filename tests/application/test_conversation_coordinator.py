"""Concurrency, shutdown and durable retry behavior of community coordination."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from opsmesh.application.conversations import configuration
from opsmesh.application.conversations import coordinator as coordinator_module
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots import conversations as queue_module

ALLOWED = {"status": "ok", "action": "allow"}


@pytest_asyncio.fixture
async def harness(tmp_path, inbox_factory):
    group = configuration.ConversationBinding(
        bot_id="qq-community",
        channel_id="group:one",
        principal_id="community-user",
        agent_ref="chat@1",
        knowledge_collection="knowledge-one",
    )
    other = group.model_copy(update={"channel_id": "group:two"})
    settings = configuration.ConversationOptions(
        data_directory=tmp_path, bindings=(group, other)
    ).model_copy(update={"poll_seconds": 0.001, "batch_delay_seconds": 0})
    clock = [datetime.now(UTC)]
    queue = await inbox_factory(tmp_path / "queue.db", clock=lambda: clock[0])
    platform = SimpleNamespace(
        run=AsyncMock(), send=AsyncMock(), recall=AsyncMock(), capture=AsyncMock()
    )
    moderation = SimpleNamespace(review=AsyncMock())
    coordinator = coordinator_module.ConversationCoordinator(settings, queue, platform, moderation)
    coordinator.moderation_worker = SimpleNamespace(tick=AsyncMock())
    coordinator.activity = SimpleNamespace(tick=AsyncMock())

    async def observe(_group, batch):
        await queue.complete_batch(batch.batch_id, state="observed")

    coordinator.chat_worker = SimpleNamespace(process=AsyncMock(side_effect=observe))
    driver = OpsMeshDriver()
    await driver.start()
    try:
        yield SimpleNamespace(
            group=group,
            other=other,
            settings=settings,
            queue=queue,
            coordinator=coordinator,
            driver=driver,
            platform=platform,
            moderation=moderation,
            clock=clock,
        )
    finally:
        await coordinator.close()
        await driver.stop()


async def enqueue(harness, event_id, *, group=None, approved=True):
    group = group or harness.group
    message = queue_module.QueuedConversationMessage(
        event_id=event_id,
        bot_id=group.bot_id,
        channel_id=group.channel_id,
        user_id="member-1",
        message_id="qq-" + event_id,
        text="safe public question",
        occurred_at=harness.clock[0],
        to_me=True,
    )
    await harness.queue.enqueue(message)
    if approved:
        await harness.queue.set_moderation(event_id, ALLOWED, True)
    return message


async def batch(harness, event_id, *, group=None, terminal=True):
    group = group or harness.group
    await enqueue(harness, event_id, group=group)
    result = await harness.queue.claim_batch(group.bot_id, group.channel_id)
    if terminal:
        await harness.queue.complete_batch(result.batch_id, state="observed")
        result = await harness.queue.get_batch(result.batch_id)
    return result


async def test_slow_chat_does_not_block_moderation_or_another_group(harness):
    await enqueue(harness, "first")
    await enqueue(harness, "other", group=harness.other)
    blocked, other_done, moderation_progress = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def process(group, item):
        if group == harness.group:
            blocked.set()
            await asyncio.Event().wait()
        await harness.queue.complete_batch(item.batch_id, state="observed")
        other_done.set()

    async def moderate():
        if blocked.is_set():
            moderation_progress.set()

    harness.coordinator.chat_worker.process.side_effect = process
    harness.coordinator.moderation_worker.tick.side_effect = moderate
    harness.coordinator.start(harness.driver)
    await asyncio.wait_for(other_done.wait(), timeout=1)
    await asyncio.wait_for(moderation_progress.wait(), timeout=1)
    assert len(await harness.queue.pending_batches()) == 1
    await harness.coordinator.close()
    assert (await harness.queue.pending_batches())[0].state == "claimed"
    assert harness.driver.task_errors == []


async def test_activity_and_chat_share_group_lock(harness):
    await enqueue(harness, "first")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(_group, item):
        entered.set()
        await release.wait()
        await harness.queue.complete_batch(item.batch_id, state="observed")

    harness.coordinator.chat_worker.process.side_effect = slow
    chat = asyncio.create_task(harness.coordinator.chat_tick(harness.group))
    await entered.wait()
    activity = asyncio.create_task(harness.coordinator.activity_tick(harness.group))
    await asyncio.sleep(0)
    harness.coordinator.activity.tick.assert_not_awaited()
    release.set()
    await asyncio.gather(chat, activity)
    harness.coordinator.activity.tick.assert_awaited_once_with(harness.group)


async def test_restart_resumes_existing_batch_with_its_run_before_new_arrivals(harness):
    original = await batch(harness, "old", terminal=False)
    await harness.queue.record_run(original.batch_id, "durable-run")
    await enqueue(harness, "new")
    await harness.coordinator.chat_tick(harness.group)
    resumed, fresh = [
        call.args[1] for call in harness.coordinator.chat_worker.process.await_args_list
    ]
    assert resumed.batch_id == original.batch_id
    assert resumed.run_id == "durable-run"
    assert [item.event_id for item in resumed.messages] == ["old"]
    assert [item.event_id for item in fresh.messages] == ["new"]
    assert await harness.queue.ready_channels() == ()


async def test_batch_delay_collects_arrivals_before_claim(harness):
    harness.coordinator.settings = harness.settings.model_copy(update={"batch_delay_seconds": 0.03})
    await enqueue(harness, "one")
    task = asyncio.create_task(harness.coordinator.chat_tick(harness.group))
    await asyncio.sleep(0.005)
    await enqueue(harness, "two")
    await task
    claimed = harness.coordinator.chat_worker.process.await_args.args[1]
    assert [item.event_id for item in claimed.messages] == ["one", "two"]


async def test_timeout_keeps_run_other_failure_retains_failed_batch_and_unlocks(harness):
    original = await batch(harness, "first", terminal=False)
    await harness.queue.record_run(original.batch_id, "run-existing")
    harness.coordinator.chat_worker.process.side_effect = OpsMeshError(
        "CONVERSATION_RUN_TIMEOUT", "private model text must not be logged", retryable=True
    )
    with pytest.raises(OpsMeshError):
        await harness.coordinator.chat_tick(harness.group)
    retained = await harness.queue.get_batch(original.batch_id)
    assert retained.state == "running" and retained.run_id == "run-existing"
    await harness.queue.delete_value("conversation-retry:" + original.batch_id)
    harness.coordinator.chat_worker.process.side_effect = OpsMeshError(
        "CONVERSATION_RUN_FAILED", "raw"
    )
    await harness.coordinator.chat_tick(harness.group)
    assert (await harness.queue.get_batch(original.batch_id)).state == "failed"
    await enqueue(harness, "later")
    assert await harness.queue.ready_channels() == (
        (harness.group.bot_id, harness.group.channel_id),
    )


async def test_close_is_idempotent_and_does_not_close_queue(harness):
    harness.coordinator.start(harness.driver)
    with pytest.raises(RuntimeError, match="already started"):
        harness.coordinator.start(harness.driver)
    await harness.coordinator.close()
    await harness.coordinator.close()
    await enqueue(harness, "after-close")
    assert await harness.queue.ready_channels()


async def test_supervision_logs_only_safe_codes_and_recovers(harness, monkeypatch, caplog):
    progress = asyncio.Event()
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("SECRET-provider-body-and-chat")
        progress.set()

    monkeypatch.setattr(coordinator_module, "_backoff", lambda _failures: 0.001)
    harness.coordinator.moderation_worker.tick.side_effect = flaky
    harness.coordinator.start(harness.driver)
    await asyncio.wait_for(progress.wait(), timeout=1)
    await harness.coordinator.close()
    assert "CONVERSATION_UNEXPECTED_ERROR" in caplog.text
    assert "SECRET-provider-body-and-chat" not in caplog.text
    assert harness.driver.task_errors == []


async def test_capture_requires_confirmed_delivery_and_retains_failure_for_retry(harness):
    selected = await batch(harness, "capture", terminal=False)
    key = harness.group.capture_prefix + selected.batch_id
    payload = {"batch_id": selected.batch_id, "answer": "approved answer", "run_id": "run-one"}
    await harness.queue.set_value(key, payload)
    await harness.coordinator.capture_tick(harness.group)
    harness.platform.capture.assert_not_awaited()
    await harness.queue.record_run(selected.batch_id, "run-one")
    await harness.queue.complete_batch(
        selected.batch_id, state="replied", run_id="run-one", delivery_id="sent"
    )
    harness.platform.capture.side_effect = OpsMeshError(
        "UNAVAILABLE", "private error", retryable=True
    )
    await harness.coordinator.capture_tick(harness.group)
    saved = await harness.queue.get_value(key)
    assert saved["answer"] == payload["answer"] and saved["attempts"] == 1
    assert datetime.fromisoformat(saved["next_attempt_at"]) > datetime.now(UTC)
    harness.platform.capture.side_effect = None
    await harness.coordinator.capture_tick(harness.other)
    assert harness.platform.capture.await_count == 1
    await harness.coordinator.capture_tick(harness.group)
    assert harness.platform.capture.await_count == 1
    await harness.queue.set_value(key, payload)
    await harness.coordinator.capture_tick(harness.group)
    assert await harness.queue.get_value(key) is None
    assert harness.platform.capture.await_args.args[2:] == ("approved answer", "run-one")


async def test_failed_capture_does_not_block_another_confirmed_record(harness):
    records = []
    for event in ("first-capture", "second-capture"):
        selected = await batch(harness, event, terminal=False)
        await harness.queue.record_run(selected.batch_id, event)
        await harness.queue.complete_batch(
            selected.batch_id, state="replied", run_id=event, delivery_id=event
        )
        key = harness.group.capture_prefix + selected.batch_id
        await harness.queue.set_value(
            key, {"batch_id": selected.batch_id, "answer": "answer", "run_id": event}
        )
        records.append(key)
    harness.platform.capture.side_effect = [OpsMeshError("UNAVAILABLE", "raw"), None]
    await harness.coordinator.capture_tick(harness.group)
    assert harness.platform.capture.await_count == 2
    assert await harness.queue.list_values(harness.group.capture_prefix) == ()
    failed = await harness.queue.list_values("conversation-capture-failed:")
    assert len(failed) == 1
    assert failed[0][1]["error_code"] == "UNAVAILABLE"


async def test_maintenance_retains_pending_capture_sources(harness):
    harness.clock[0] = datetime.now(UTC) - timedelta(days=20)
    selected = await batch(harness, "retained", terminal=False)
    await harness.queue.record_run(selected.batch_id, "run-capture")
    await harness.queue.complete_batch(
        selected.batch_id, state="replied", run_id="run-capture", delivery_id="delivery"
    )
    key = harness.group.capture_prefix + selected.batch_id
    await harness.queue.set_value(
        key, {"batch_id": selected.batch_id, "answer": "safe", "run_id": "run-capture"}
    )
    await harness.coordinator.maintenance_tick()
    assert (await harness.queue.get_batch(selected.batch_id)).messages
    await harness.queue.delete_value(key)
    await harness.coordinator.maintenance_tick()
    with pytest.raises(KeyError):
        await harness.queue.get_batch(selected.batch_id)


async def test_capture_pagination_advances_past_a_full_deferred_page(harness):
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for index in range(128):
        await harness.queue.set_value(
            harness.group.capture_prefix + f"aaa{index:03}",
            {"batch_id": "deferred", "answer": "safe", "run_id": "r", "next_attempt_at": deadline},
        )
    selected = await batch(harness, "ready-later", terminal=False)
    await harness.queue.record_run(selected.batch_id, "run-ready")
    await harness.queue.complete_batch(
        selected.batch_id, state="replied", run_id="run-ready", delivery_id="delivery"
    )
    key = harness.group.capture_prefix + "zzz"
    await harness.queue.set_value(
        key, {"batch_id": selected.batch_id, "answer": "answer", "run_id": "run-ready"}
    )
    await harness.coordinator.capture_tick(harness.group)
    harness.platform.capture.assert_not_awaited()
    await harness.coordinator.capture_tick(harness.group)
    harness.platform.capture.assert_awaited_once()
    assert await harness.queue.get_value(key) is None


async def test_permanent_capture_failure_retains_source_and_is_not_retried(harness):
    harness.clock[0] = datetime.now(UTC) - timedelta(days=20)
    selected = await batch(harness, "permanent", terminal=False)
    await harness.queue.record_run(selected.batch_id, "r")
    await harness.queue.complete_batch(
        selected.batch_id, state="replied", run_id="r", delivery_id="d"
    )
    await harness.queue.set_value(
        harness.group.capture_prefix + selected.batch_id,
        {"batch_id": selected.batch_id, "answer": "approved", "run_id": "r"},
    )
    harness.platform.capture.side_effect = OpsMeshError("FORBIDDEN", "private provider text")
    await harness.coordinator.capture_tick(harness.group)
    await harness.coordinator.capture_tick(harness.group)
    harness.platform.capture.assert_awaited_once()
    await harness.coordinator.maintenance_tick()
    assert (await harness.queue.get_batch(selected.batch_id)).messages
    failed = await harness.queue.list_values("conversation-capture-failed:")
    assert failed[0][1]["answer"] == "approved"
    assert "private provider text" not in str(failed)


async def test_transient_retries_are_bounded_and_terminal_run_errors_stay_terminal(harness):
    selected = await batch(harness, "retry-limited", terminal=False)
    harness.coordinator.chat_worker.process.side_effect = [
        OpsMeshError("BOT_RATE_LIMITED" if index % 2 else "BOT_UNAVAILABLE", "raw", retryable=True)
        for index in range(8)
    ]
    key = "conversation-retry:" + selected.batch_id
    for attempt in range(8):
        saved = await harness.queue.get_value(key)
        if saved:
            saved["next_attempt_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            await harness.queue.set_value(key, saved)
        if attempt < 7:
            with pytest.raises(OpsMeshError):
                await harness.coordinator.chat_tick(harness.group)
        else:
            await harness.coordinator.chat_tick(harness.group)
    assert (await harness.queue.get_batch(selected.batch_id)).state == "failed"
    assert harness.coordinator.chat_worker.process.await_count == 8
    assert await harness.queue.get_value(key) is None
    other = await batch(harness, "terminal", terminal=False)
    harness.coordinator.chat_worker.process.side_effect = OpsMeshError(
        "CONVERSATION_RUN_CANCELLED", "cancelled", retryable=True
    )
    await harness.coordinator.chat_tick(harness.group)
    assert (await harness.queue.get_batch(other.batch_id)).state == "failed"
