"""Proactive group participation is tested against durable local state and fake services."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio

from opsmesh.application.conversations import activity as activity_module
from opsmesh.application.conversations import configuration as configuration_module
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots import conversations as queue_module

START = datetime(2026, 11, 1, 2, tzinfo=UTC)
ALLOWED = {"status": "ok", "action": "allow", "reason_code": "below_threshold"}
BLOCKED = {"status": "ok", "action": "remove", "reason_code": "policy_threshold"}


class FakeModeration:
    def __init__(self):
        self.reviews = []
        self.decision, self.review_action = "reply", "allow"

    async def review(self, text, *, context=()):
        self.reviews.append((text, context))
        return SimpleNamespace(status="ok", action=self.review_action)


class FakePlatform:
    def __init__(self, queue):
        self.queue, self.runs, self.sends = queue, [], []
        self.output, self.send_error, self.on_run = (
            "A short helpful follow-up.",
            None,
            None,
        )

    async def run(self, group, agent_ref, prompt, key, *, purpose, on_submitted=None):
        saved = await self.queue.get_value(activity_module.ConversationActivity._key(group))
        assert saved["outcome"] == "reserved" and saved["attempts"] >= 1
        self.runs.append((prompt, key, purpose))
        if on_submitted:
            await on_submitted(f"run-{len(self.runs)}")
        if self.on_run:
            await self.on_run()
        return SimpleNamespace(id=f"run-{len(self.runs)}", status="completed", output=self.output)

    async def send(self, group, text, key, *, message_id=None):
        saved = await self.queue.get_value(activity_module.ConversationActivity._key(group))
        assert saved["outcome"] == "sending"
        self.sends.append((text, key, message_id))
        if self.send_error:
            raise self.send_error
        return SimpleNamespace(delivery_id=f"delivery-{len(self.sends)}")


@pytest_asyncio.fixture
async def harness(tmp_path, inbox_factory):
    now = [START]
    group = configuration_module.ConversationBinding(
        bot_id="bot-one",
        channel_id="group:one",
        principal_id="principal",
        agent_ref="agent@1",
        proactive_enabled=True,
        knowledge_collection="group-faq",
    )
    settings = configuration_module.ConversationOptions(data_directory=tmp_path, bindings=(group,))
    queue = await inbox_factory(tmp_path / "activity.db", clock=lambda: now[0])
    platform, moderation = FakePlatform(queue), FakeModeration()
    activity = activity_module.ConversationActivity(
        settings, queue, platform, moderation, clock=lambda: now[0]
    )
    result = SimpleNamespace(
        now=now,
        group=group,
        settings=settings,
        queue=queue,
        platform=platform,
        moderation=moderation,
        activity=activity,
    )
    yield result


async def message(harness, identifier, *, text="Can we add a useful idea?", state="observed"):
    h = harness
    item = queue_module.QueuedConversationMessage(
        event_id=identifier,
        bot_id=h.group.bot_id,
        channel_id=h.group.channel_id,
        user_id="member-one",
        message_id=f"provider-{identifier}",
        text=text,
        occurred_at=h.now[0],
        to_me=False,
    )
    await h.queue.enqueue(item)
    if state == "pending":
        return
    await h.queue.set_moderation(
        identifier, BLOCKED if state == "blocked" else ALLOWED, state != "blocked"
    )
    if state in {"observed", "active"}:
        batch = await h.queue.claim_batch(h.group.bot_id, h.group.channel_id)
        if state == "observed":
            await h.queue.complete_batch(batch.batch_id, state="observed")


async def ready(h):
    await h.activity.tick(h.group)
    h.now[0] += timedelta(hours=1, seconds=1)
    await message(h, "new-question")


async def test_startup_cooldown_then_confirmed_proactive_delivery(harness):
    h = harness
    await message(h, "at-startup")
    await h.activity.tick(h.group)
    assert not h.platform.sends
    h.now[0] += timedelta(hours=1, seconds=1)
    await message(h, "new-question")
    await message(h, "blocked", text="unapproved-promotion", state="blocked")
    await h.activity.tick(h.group)
    assert len(h.platform.runs) == len(h.platform.sends) == 1
    assert h.platform.runs[0][2] == "activity"
    assert "unapproved-promotion" not in h.platform.runs[0][0]
    assert h.platform.sends[0][2] is None
    assert h.moderation.reviews[0][0] == h.platform.output
    saved = await h.queue.get_value(h.activity._key(h.group))
    assert saved["outcome"] == "sent" and saved["delivery_id"] == "delivery-1"
    assert saved["run_id"] == "run-1" and saved["attempts"] == 1
    assert await h.queue.last_reply(h.group.bot_id, h.group.channel_id) == h.now[0]


@pytest.mark.parametrize("hour", [15, 23])
async def test_local_quiet_hours_do_not_call_models(harness, hour):
    h = harness
    await h.activity.tick(h.group)
    h.now[0] = START.replace(hour=hour)
    await message(h, "quiet-question")
    await h.activity.tick(h.group)
    assert not h.platform.runs


@pytest.mark.parametrize(
    "condition", ["no-activity", "stale", "recent-reply", "ready", "active", "pending"]
)
async def test_no_activity_or_regular_conversation_takes_priority(harness, condition):
    h = harness
    await h.activity.tick(h.group)
    h.now[0] += timedelta(hours=1, seconds=1)
    if condition != "no-activity":
        await message(
            h,
            "question",
            state=condition if condition in {"ready", "active", "pending"} else "observed",
        )
    if condition == "stale":
        h.now[0] += timedelta(seconds=1801)
    if condition == "recent-reply":
        await h.queue.record_reply(h.group.bot_id, h.group.channel_id)
    await h.activity.tick(h.group)
    assert not h.platform.sends


async def test_daily_attempt_limit_is_reserved_before_any_external_side_effect(harness):
    h = harness
    await h.activity.tick(h.group)
    for index in range(5):
        h.now[0] += timedelta(hours=1, seconds=1)
        await message(h, f"question-{index}")
        await h.activity.tick(h.group)
    assert len(h.platform.sends) == 4
    assert len({call[1] for call in h.platform.sends}) == 4
    assert (await h.queue.get_value(h.activity._key(h.group)))["attempts"] == 4


async def test_restart_keeps_quota_and_establishes_new_startup_cooldown(harness):
    h = harness
    await ready(h)
    await h.activity.tick(h.group)
    h.now[0] += timedelta(minutes=5)
    restarted = activity_module.ConversationActivity(
        h.settings, h.queue, h.platform, h.moderation, clock=lambda: h.now[0]
    )
    await message(h, "after-restart")
    await restarted.tick(h.group)
    assert len(h.platform.sends) == 1
    h.now[0] += timedelta(hours=1, seconds=1)
    await message(h, "later")
    await restarted.tick(h.group)
    assert len(h.platform.sends) == 2
    assert (await h.queue.get_value(restarted._key(h.group)))["attempts"] == 2
    h.now[0] += timedelta(days=1)
    await message(h, "next-day")
    await restarted.tick(h.group)
    assert (await h.queue.get_value(restarted._key(h.group)))["attempts"] == 1


async def test_delivery_unknown_never_retries_same_source_after_cooldown(harness):
    h = harness
    settings = h.settings.model_copy(update={"proactive_recent_activity_seconds": 7200})
    h.activity = activity_module.ConversationActivity(
        settings, h.queue, h.platform, h.moderation, clock=lambda: h.now[0]
    )
    await ready(h)
    h.platform.send_error = OpsMeshError("DELIVERY_UNKNOWN", "private-provider-info")
    await h.activity.tick(h.group)
    h.now[0] += timedelta(hours=1, seconds=1)
    await h.activity.tick(h.group)
    assert len(h.platform.sends) == 1
    saved = await h.queue.get_value(h.activity._key(h.group))
    assert saved["error_code"] == "DELIVERY_UNKNOWN" and saved["attempts"] == 1
    assert "private-provider-info" not in str(saved)
    assert await h.queue.last_reply(h.group.bot_id, h.group.channel_id) is None


@pytest.mark.parametrize("outcome", ["oversized", "unsafe", "observe", "unknown-output"])
async def test_unsuitable_or_unreviewed_output_is_never_sent(harness, outcome):
    h = harness
    await ready(h)
    if outcome == "oversized":
        h.platform.output = "x" * (h.settings.reply_character_limit + 1)
    elif outcome == "unsafe":
        h.moderation.review_action = "remove"
    elif outcome == "observe":
        h.platform.output = ""
    else:
        h.platform.output = {"unstructured": "not a final reply"}
    await h.activity.tick(h.group)
    assert not h.platform.sends
    assert (await h.queue.get_value(h.activity._key(h.group)))["attempts"] == 1


async def test_new_human_message_during_generation_abandons_proactive_output(harness):
    h = harness
    await ready(h)

    async def new_message():
        h.now[0] += timedelta(seconds=1)
        await message(h, "arrived-during-run", state="pending")

    h.platform.on_run = new_message
    await h.activity.tick(h.group)
    assert len(h.platform.runs) == 1 and not h.platform.sends
    assert (await h.queue.get_value(h.activity._key(h.group)))["outcome"] == "stale"


async def test_concurrent_ticks_do_not_duplicate_reservation_or_delivery(harness):
    h = harness
    await ready(h)
    await asyncio.gather(*(h.activity.tick(h.group) for _ in range(8)))
    assert len(h.platform.runs) == len(h.platform.sends) == 1
    assert (await h.queue.get_value(h.activity._key(h.group)))["attempts"] == 1
