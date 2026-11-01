"""Durable queue behavior across busy channels, duplicate callbacks and restarts."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from opsmesh.modules.bots import conversations as queue_module

START = datetime(2026, 11, 1, tzinfo=UTC)
ALLOWED = {"status": "ok", "action": "allow", "reason_code": "below_threshold"}
REJECTED = {"status": "ok", "action": "remove", "reason_code": "policy_threshold"}
UNKNOWN = {"status": "unknown", "action": "review", "reason_code": "provider_unavailable"}


def message(event_id, text="a message", *, bot_id="bot-1", channel_id="group:one", **updates):
    return queue_module.QueuedConversationMessage(
        event_id=event_id,
        bot_id=bot_id,
        channel_id=channel_id,
        user_id="member-1",
        message_id="provider-" + event_id,
        text=text,
        occurred_at=START,
        to_me=updates.get("to_me", False),
        has_attachments=updates.get("has_attachments", False),
    )


@pytest_asyncio.fixture
async def queue(tmp_path, inbox_factory):
    instance = await inbox_factory(tmp_path / "community.db")
    yield instance


async def allow(queue, *messages):
    for item in messages:
        await queue.enqueue(item)
        await queue.set_moderation(item.event_id, ALLOWED, True)


async def test_callback_replays_are_idempotent_and_preserve_first_contents(queue):
    original = message("duplicate", "original message")
    inserted = await asyncio.gather(*(queue.enqueue(original) for _ in range(12)))
    assert sum(inserted) == 1
    assert not await queue.enqueue(message("duplicate", "changed replay"))
    assert await queue.pending_moderation() == (original,)
    assert await queue.ready_channels() == ()


async def test_uncertain_rejected_and_unmoderated_messages_never_become_chat_context(queue):
    for event_id in ("allowed", "unknown", "rejected", "pending"):
        await queue.enqueue(message(event_id, text=event_id))
    await queue.set_moderation("allowed", ALLOWED, True)
    await queue.set_moderation("unknown", UNKNOWN, True)
    await queue.set_moderation("rejected", REJECTED, True)
    assert [item.event_id for item in await queue.pending_moderation()] == ["pending"]
    assert [item.text for item in await queue.recent_messages("bot-1", "group:one")] == ["allowed"]
    batch = await queue.claim_batch("bot-1", "group:one")
    assert [item.event_id for item in batch.messages] == ["allowed"]


async def test_messages_arriving_while_busy_join_only_the_next_batch(queue):
    await allow(queue, message("first"), message("second"))
    batch = await queue.claim_batch("bot-1", "group:one")
    await queue.record_run(batch.batch_id, "run-1")
    await allow(queue, message("third"), message("fourth"))
    assert await queue.claim_batch("bot-1", "group:one") is None
    assert await queue.ready_channels() == ()
    assert [item.event_id for item in (await queue.get_batch(batch.batch_id)).messages] == [
        "first",
        "second",
    ]
    await queue.complete_batch(batch.batch_id, run_id="run-1", delivery_id="delivery-1")
    assert await queue.ready_channels() == (("bot-1", "group:one"),)
    next_batch = await queue.claim_batch("bot-1", "group:one")
    assert next_batch.batch_id != batch.batch_id
    assert [item.event_id for item in next_batch.messages] == ["third", "fourth"]


async def test_restart_recovers_stable_batch_members_and_run_identity(tmp_path, inbox_factory):
    path = tmp_path / "durable.db"
    first = await inbox_factory(path)
    await allow(first, message("before-restart"))
    original = await first.claim_batch("bot-1", "group:one")
    await first.record_run(original.batch_id, "durable-run")
    await first.record_error(original.batch_id, "DELIVERY_UNKNOWN")
    await allow(first, message("arrived-later"))
    recovered = await inbox_factory(path)
    batches = await recovered.pending_batches()
    assert len(batches) == 1 and batches[0].batch_id == original.batch_id
    assert batches[0].run_id == "durable-run" and batches[0].state == "running"
    assert batches[0].error_code == "DELIVERY_UNKNOWN"
    assert [item.event_id for item in batches[0].messages] == ["before-restart"]
    assert await recovered.claim_batch("bot-1", "group:one") is None
    await recovered.complete_batch(original.batch_id, delivery_id="confirmed-delivery")
    next_batch = await recovered.claim_batch("bot-1", "group:one")
    assert [item.event_id for item in next_batch.messages] == ["arrived-later"]


async def test_bot_and_group_reservations_are_isolated(queue):
    await allow(
        queue,
        message("one"),
        message("two", channel_id="group:two"),
        message("another-bot", bot_id="bot-2"),
    )
    channels = await queue.ready_channels()
    assert set(channels) == {("bot-1", "group:one"), ("bot-1", "group:two"), ("bot-2", "group:one")}
    batches = await asyncio.gather(*(queue.claim_batch(*channel) for channel in channels))
    assert len({batch.batch_id for batch in batches}) == 3
    assert {batch.messages[0].event_id for batch in batches} == {"one", "two", "another-bot"}


async def test_simultaneous_claims_cannot_split_one_channel_into_two_active_batches(queue):
    await allow(queue, message("one"), message("two"))
    claims = await asyncio.gather(*(queue.claim_batch("bot-1", "group:one") for _ in range(5)))
    claimed = [batch for batch in claims if batch is not None]
    assert len(claimed) == 1 and claimed[0].messages[0].event_id == "one"
    assert len(claimed[0].messages) == 2


async def test_batch_limits_keep_boundaries_without_dropping_oversized_messages(queue):
    await allow(
        queue, message("large", "x" * 2000), message("second", "abc"), message("third", "de")
    )
    large = await queue.claim_batch("bot-1", "group:one", max_chars=1000)
    assert large.oversized and len(large.messages) == 1
    assert large.messages[0].text == "x" * 2000
    await queue.complete_batch(large.batch_id, state="review")
    combined = await queue.claim_batch("bot-1", "group:one", max_chars=1000)
    assert not combined.oversized
    assert [item.event_id for item in combined.messages] == ["second", "third"]
    assert len("\n".join(item.text for item in combined.messages)) == 6


async def test_claim_has_no_fixed_message_count_limit(queue):
    arrivals = [message(f"short-{index}", "hi") for index in range(60)]
    await allow(queue, *arrivals)
    combined = await queue.claim_batch("bot-1", "group:one")
    assert combined.messages == tuple(arrivals)
    await queue.complete_batch(combined.batch_id, state="observed")
    assert await queue.ready_channels() == ()


async def test_context_budget_keeps_unclaimed_messages_and_reserves_attachment_space(queue):
    arrivals = [message(f"image-{index}", "", has_attachments=True) for index in range(3)]
    await allow(queue, *arrivals)
    first = await queue.claim_batch("bot-1", "group:one", max_chars=4000)
    assert first.messages == (arrivals[0],) and not first.oversized
    await queue.complete_batch(first.batch_id, state="observed")
    second = await queue.claim_batch("bot-1", "group:one", max_chars=8000)
    assert second.messages == tuple(arrivals[1:]) and not second.oversized


async def test_run_and_terminal_delivery_ids_cannot_be_replaced(queue):
    await allow(queue, message("one"))
    batch = await queue.claim_batch("bot-1", "group:one")
    await queue.record_run(batch.batch_id, "first-run")
    await queue.record_run(batch.batch_id, "first-run")
    with pytest.raises(ValueError, match="two different runs"):
        await queue.record_run(batch.batch_id, "second-run")
    with pytest.raises(ValueError, match="different run"):
        await queue.complete_batch(batch.batch_id, run_id="second-run")
    await queue.complete_batch(batch.batch_id, delivery_id="first-delivery")
    await queue.complete_batch(batch.batch_id, delivery_id="first-delivery")
    with pytest.raises(ValueError, match="finalized"):
        await queue.complete_batch(batch.batch_id, delivery_id="other-delivery")
    with pytest.raises(ValueError, match="finalized"):
        await queue.complete_batch(batch.batch_id, state="observed")


async def test_cooldowns_use_received_time_and_idempotent_confirmed_reply(tmp_path, inbox_factory):
    now = [START]
    queue = await inbox_factory(tmp_path / "clock.db", clock=lambda: now[0])
    await queue.record_reply("bot-1", "group:one")
    assert await queue.last_activity("bot-1", "group:one") is None
    now[0] += timedelta(minutes=1)
    await allow(queue, message("old-provider-timestamp"))
    assert await queue.last_activity("bot-1", "group:one") == now[0]
    original_activity = now[0]
    now[0] += timedelta(minutes=1)
    assert not await queue.enqueue(message("old-provider-timestamp"))
    assert await queue.last_activity("bot-1", "group:one") == original_activity
    batch = await queue.claim_batch("bot-1", "group:one")
    await queue.complete_batch(batch.batch_id, delivery_id="sent-once")
    confirmed_reply = now[0]
    now[0] += timedelta(hours=1)
    await queue.complete_batch(batch.batch_id, delivery_id="sent-once")
    assert await queue.last_reply("bot-1", "group:one") == confirmed_reply
    assert await queue.known_channels() == (("bot-1", "group:one"),)


async def test_failed_work_remains_inspectable_and_is_not_purged(queue):
    await allow(queue, message("failed"))
    failed = await queue.claim_batch("bot-1", "group:one")
    await allow(queue, message("new"))
    await queue.mark_failed(failed.batch_id, "RUN_TERMINAL_FAILURE")
    assert await queue.pending_batches() == ()
    retained = await queue.pending_batches(include_failed=True)
    assert len(retained) == 1 and retained[0].error_code == "RUN_TERMINAL_FAILURE"
    assert await queue.purge_before(datetime.now(UTC) + timedelta(days=1)) == 0
    assert (await queue.get_batch(failed.batch_id)).messages[0].event_id == "failed"
    assert (await queue.claim_batch("bot-1", "group:one")).messages[0].event_id == "new"


async def test_retention_purges_only_terminal_old_messages(tmp_path, inbox_factory):
    now = [START]
    queue = await inbox_factory(tmp_path / "retention.db", clock=lambda: now[0])
    await allow(queue, message("complete"))
    completed = await queue.claim_batch("bot-1", "group:one")
    await queue.complete_batch(completed.batch_id, state="observed")
    await queue.enqueue(message("rejected"))
    await queue.set_moderation("rejected", REJECTED, False)
    await queue.enqueue(message("pending"))
    await allow(queue, message("queued"), message("active", channel_id="group:two"))
    active = await queue.claim_batch("bot-1", "group:two")
    now[0] += timedelta(days=2)
    assert await queue.purge_before(START + timedelta(days=1)) == 2
    with pytest.raises(KeyError):
        await queue.get_batch(completed.batch_id)
    assert [item.event_id for item in await queue.pending_moderation()] == ["pending"]
    assert (await queue.get_batch(active.batch_id)).messages[0].event_id == "active"
    assert (await queue.claim_batch("bot-1", "group:one")).messages[0].event_id == "queued"


async def test_moderation_finalization_and_unknown_metadata_are_rejected(queue):
    await queue.enqueue(message("one"))
    with pytest.raises(ValueError, match="result fields"):
        await queue.set_moderation("one", {**ALLOWED, "api_key": "not-allowed"}, True)
    await queue.set_moderation("one", ALLOWED, True)
    await queue.set_moderation("one", ALLOWED, True)
    with pytest.raises(ValueError, match="finalized"):
        await queue.set_moderation("one", REJECTED, False)
    batch = await queue.claim_batch("bot-1", "group:one")
    with pytest.raises(ValueError, match="machine error code"):
        await queue.record_error(batch.batch_id, "A raw exception with private content")


async def test_identifiers_and_text_are_sql_parameters(queue):
    hostile_id = "message'); DROP TABLE community_messages; --"
    await allow(queue, message(hostile_id, text="literal 'quotes'; --"))
    batch = await queue.claim_batch("bot-1", "group:one")
    assert batch.messages[0].event_id == hostile_id
    await queue.enqueue(message("still-present"))
    assert (await queue.pending_moderation())[0].event_id == "still-present"


def test_message_size_and_timezone_requirements_are_enforced():
    with pytest.raises(ValueError, match="16000"):
        message("oversized", "a" * 16_001)
    with pytest.raises(ValueError, match="timezone"):
        queue_module.QueuedConversationMessage(
            event_id="event",
            bot_id="bot",
            channel_id="channel",
            user_id="user",
            message_id="message",
            text="hello",
            occurred_at=datetime(2026, 11, 1),
            to_me=False,
        )


async def test_coordination_state_survives_restart_and_pages_pending_capture_jobs(
    tmp_path, inbox_factory
):
    path = tmp_path / "state.db"
    first = await inbox_factory(path)
    assert await first.get_value("session:one") is None
    await first.set_value("session:one", {"session_id": "session-1"})
    await first.set_value("faq:a", {"state": "running", "run_id": "faq-run-a"})
    await first.set_value("faq:b", {"state": "pending", "batch_id": "batch-b"})
    await first.set_value("faq_%:literal", {"state": "pending"})
    recovered = await inbox_factory(path)
    assert await recovered.get_value("session:one") == {"session_id": "session-1"}
    page = await recovered.list_values("faq:", limit=1)
    assert page == (("faq:a", {"state": "running", "run_id": "faq-run-a"}),)
    assert (await recovered.list_values("faq:", limit=1, after=page[-1][0]))[0][0] == "faq:b"
    assert [item[0] for item in await recovered.list_values("faq_%:")] == ["faq_%:literal"]
    await recovered.delete_value("faq:a")
    await recovered.delete_value("faq:a")
    assert await recovered.get_value("faq:a") is None


async def test_coordination_state_is_copied_on_write_and_credentials_are_refused(queue):
    state = {"session_id": "original", "daily": {"count": 1}}
    await queue.set_value("session:one", state)
    state["session_id"] = "mutated-in-memory"
    assert (await queue.get_value("session:one"))["session_id"] == "original"
    await queue.set_value("session:one", {"session_id": "updated"})
    assert await queue.get_value("session:one") == {"session_id": "updated"}
    with pytest.raises(ValueError, match="credentials"):
        await queue.set_value("provider", {"nested": {"api_key": "must-not-be-stored"}})
    assert await queue.get_value("provider") is None
