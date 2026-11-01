"""Moderation and conversation workers against durable SQLite and controlled providers."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio

from opsmesh.application.conversations import chat as chat_worker
from opsmesh.application.conversations import configuration as configuration_module
from opsmesh.application.conversations import contracts as moderation_module
from opsmesh.application.conversations import moderation as moderation_worker
from opsmesh.application.conversations.coordinator import ConversationCoordinator
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots import conversations as queue_module


def decision(action="allow", *, status="ok"):
    return moderation_module.ReviewDecision(
        status=status,
        action=action,
        reason="违反广告规则" if action == "remove" else "review-result",
        reason_code="policy_threshold" if action == "remove" else "below_threshold",
    )


class FakeModeration:
    def __init__(self):
        self.reviews, self.decisions = [], {}

    async def review(self, text, *, context=()):
        self.reviews.append((text, context))
        return self.decisions.get(text, decision())


class FakePlatform:
    def __init__(self, queue):
        self.queue = queue
        self.recalls, self.runs, self.sends = [], [], []
        self.delivered, self.run_ledger = {}, {}
        self.recall_error, self.send_error, self.on_run = None, None, None
        self.output = "A reviewed and helpful answer."

    async def recall(self, group, message_id):
        self.recalls.append(message_id)
        if self.recall_error:
            raise self.recall_error

    async def run(
        self, group, agent_ref, prompt, key, *, purpose, existing_run_id=None, on_submitted=None
    ):
        self.runs.append((prompt, key, existing_run_id, purpose))
        run = self.run_ledger.setdefault(
            key,
            SimpleNamespace(
                id=f"run-{len(self.run_ledger) + 1}",
                status="completed",
                output=self.output,
            ),
        )
        assert existing_run_id is None or existing_run_id == run.id
        if on_submitted:
            await on_submitted(run.id)
        if self.on_run:
            await self.on_run()
        return run

    async def send(self, group, text, key, *, message_id=None):
        self.sends.append((text, key, message_id))
        if self.send_error:
            error, self.send_error = self.send_error, None
            raise error
        if key in self.delivered:
            previous_text, previous_target, receipt = self.delivered[key]
            assert (previous_text, previous_target) == (text, message_id)
            return receipt
        receipt = SimpleNamespace(
            delivery_id=f"delivery-{len(self.delivered) + 1}", message_id="sent"
        )
        self.delivered[key] = (text, message_id, receipt)
        return receipt


@pytest_asyncio.fixture
async def harness(tmp_path, inbox_factory):
    group = configuration_module.ConversationBinding(
        bot_id="community-bot",
        channel_id="group:one",
        principal_id="restricted-principal",
        agent_ref="community-agent@1",
        knowledge_collection="faq",
    )
    settings = configuration_module.ConversationOptions(
        data_directory=tmp_path,
        bindings=(group,),
        removed_notice="消息因{reason}已撤回",
        unconfirmed_notice="消息涉及{reason}, 未确认撤回成功",
    )
    queue = await inbox_factory(tmp_path / "workers.db")
    platform, moderation = FakePlatform(queue), FakeModeration()
    result = SimpleNamespace(
        group=group,
        settings=settings,
        queue=queue,
        platform=platform,
        moderation=moderation,
        moderation_worker=moderation_worker.ConversationModerationWorker(
            settings, queue, platform, moderation
        ),
        chat=chat_worker.ConversationChatWorker(settings, queue, platform, moderation),
    )
    yield result


async def enqueue(h, event_id, text="A public question", *, allowed=False, **updates):
    message = queue_module.QueuedConversationMessage(
        event_id=event_id,
        bot_id=h.group.bot_id,
        channel_id=h.group.channel_id,
        user_id=updates.get("user_id", "private-member-openid-one"),
        message_id=f"provider-{event_id}",
        text=text,
        occurred_at=updates.get("occurred_at", datetime.now(UTC) - timedelta(seconds=1)),
        to_me=updates.get("to_me", False),
        has_attachments=updates.get("has_attachments", False),
    )
    await h.queue.enqueue(message)
    if allowed:
        await h.queue.set_moderation(event_id, decision().model_dump(mode="json"), True)
    return message


async def batch(h):
    return await h.queue.claim_batch(h.group.bot_id, h.group.channel_id)


async def test_busy_group_drains_entire_backlog_in_one_followup_model_run(harness):
    h = harness
    settings = h.settings.model_copy(update={"batch_delay_seconds": 0, "poll_seconds": 10})
    coordinator = ConversationCoordinator(settings, h.queue, h.platform, h.moderation)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_first_run():
        if len(h.platform.runs) == 1:
            entered.set()
            await release.wait()

    h.platform.on_run = slow_first_run
    await enqueue(h, "initial", "hello", allowed=True)
    task = asyncio.create_task(coordinator.chat_tick(h.group))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        arrivals = [
            await enqueue(h, f"queued-{index}", "hello", allowed=True) for index in range(60)
        ]
        coordinator.settings = settings.model_copy(update={"batch_delay_seconds": 10})
        release.set()
        await asyncio.wait_for(task, 5)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(h.platform.runs) == len(h.platform.sends) == 2
    assert len(json.loads(h.platform.runs[0][0])["messages"]) == 1
    following = json.loads(h.platform.runs[1][0])["messages"]
    assert [item["event_id"] for item in following] == [item.event_id for item in arrivals]
    assert await h.queue.ready_channels() == () and await h.queue.pending_batches() == ()


async def test_moderation_confirmed_recall_reports_only_confirmed_outcome(harness):
    h = harness
    original = await enqueue(h, "advertisement", "private-ad-link-and-message")
    h.moderation.decisions[original.text] = decision("remove")
    await h.moderation_worker.tick()
    saved = await h.queue.get_value(f"moderation:{original.event_id}")
    assert h.platform.recalls == [original.message_id] and saved["recall"] == "confirmed"
    assert "已撤回" in h.platform.sends[0][0] and original.text not in h.platform.sends[0][0]
    assert saved["notice_delivery_id"] == "delivery-1"
    assert await h.queue.ready_channels() == () and await h.queue.pending_moderation() == ()
    await h.moderation_worker.tick()
    assert len(h.platform.recalls) == len(h.platform.sends) == 1


@pytest.mark.parametrize(
    "code", ["BOT_PERMISSION_DENIED", "BOT_MESSAGE_EXPIRED", "DELIVERY_UNKNOWN"]
)
async def test_moderation_recall_failure_keeps_provider_evidence_and_never_claims_success(
    harness, code
):
    h = harness
    message = await enqueue(h, "remove")
    h.moderation.decisions[message.text] = decision("remove")
    h.platform.recall_error = OpsMeshError(
        code, "private-provider-details", provider="qq", provider_code="40062003"
    )
    await h.moderation_worker.tick()
    saved = await h.queue.get_value(f"moderation:{message.event_id}")
    assert saved["recall"] == "failed" and saved["error_code"] == code
    assert saved["provider_code"] == "40062003"
    assert "已撤回" not in h.platform.sends[0][0]
    assert "private-provider-details" not in str(saved)
    assert await h.queue.pending_moderation() == () and await h.queue.ready_channels() == ()


@pytest.mark.parametrize("age", [120, -60])
async def test_expired_or_future_message_does_not_attempt_recall(harness, age):
    h = harness
    message = await enqueue(h, "old", occurred_at=datetime.now(UTC) - timedelta(seconds=age))
    h.moderation.decisions[message.text] = decision("remove")
    await h.moderation_worker.tick()
    assert not h.platform.recalls
    assert (await h.queue.get_value(f"moderation:{message.event_id}"))["recall"] == "expired"
    assert "已撤回" not in h.platform.sends[0][0]


@pytest.mark.parametrize("case", ["attachment", "empty", "unknown"])
async def test_unknown_and_unsupported_input_requires_review_without_recall_or_chat(harness, case):
    h = harness
    message = await enqueue(
        h,
        case,
        text="" if case == "empty" else "Uncertain text",
        has_attachments=case == "attachment",
    )
    if case == "unknown":
        h.moderation.decisions[message.text] = decision("review", status="unknown")
    await h.moderation_worker.tick()
    assert not h.platform.recalls and not h.platform.sends
    assert (
        await h.queue.ready_channels() == ()
        and await h.queue.recent_messages(h.group.bot_id, h.group.channel_id) == ()
    )
    assert len(h.moderation.reviews) == (1 if case == "unknown" else 0)


@pytest.mark.parametrize("saved_outcome", ["attempting", "confirmed"])
async def test_recovered_moderation_state_does_not_repeat_recall_or_jev(harness, saved_outcome):
    h = harness
    message = await enqueue(h, "recover")
    await h.queue.set_value(
        f"moderation:{message.event_id}",
        {
            "decision": decision("remove").model_dump(mode="json"),
            "recall": saved_outcome,
        },
    )
    recovered = moderation_worker.ConversationModerationWorker(
        h.settings, h.queue, h.platform, h.moderation
    )
    await recovered.tick()
    assert not h.platform.recalls and not h.moderation.reviews
    assert ("已撤回" in h.platform.sends[0][0]) is (saved_outcome == "confirmed")
    assert await h.queue.pending_moderation() == ()


async def test_cancellation_during_recall_leaves_durable_uncertainty(harness):
    h = harness
    message = await enqueue(h, "interrupted")
    h.moderation.decisions[message.text] = decision("remove")
    h.platform.recall_error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await h.moderation_worker.review(message)
    saved = await h.queue.get_value(f"moderation:{message.event_id}")
    assert saved["recall"] == "attempting" and len(h.platform.recalls) == 1
    h.platform.recall_error = None
    await h.moderation_worker.tick()
    assert len(h.platform.recalls) == 1 and len(h.moderation.reviews) == 1
    assert "已撤回" not in h.platform.sends[0][0]


async def test_allowed_message_context_excludes_blocked_material(harness):
    h = harness
    previous = await enqueue(h, "safe-context", "Safe prior discussion", allowed=True)
    blocked = await enqueue(h, "blocked-context", "Private blocked material")
    await h.queue.set_moderation(
        blocked.event_id, decision("remove").model_dump(mode="json"), False
    )
    latest = await enqueue(h, "question")
    await h.moderation_worker.tick()
    assert h.moderation.reviews == [(latest.text, (previous.text,))]
    assert (h.group.bot_id, h.group.channel_id) in await h.queue.ready_channels()


async def test_chat_batch_uses_one_decision_and_anonymous_consistent_senders(harness):
    h = harness
    messages = [
        await enqueue(h, "first", "First question", allowed=True),
        await enqueue(h, "second", "Second speaker", allowed=True, user_id="private-member-two"),
        await enqueue(h, "third", "Follow-up", allowed=True, to_me=True),
    ]
    selected = await batch(h)
    await h.chat.process(h.group, selected)
    assert len(h.moderation.reviews) == 1
    assert len(h.platform.runs) == len(h.platform.sends) == 1
    data = json.loads(h.platform.runs[0][0])
    assert (
        data["purpose"] == "chat"
        and data["reply_character_limit"] == h.settings.reply_character_limit
    )
    assert [row["event_id"] for row in data["messages"]] == [item.event_id for item in messages]
    senders = [row["sender"] for row in data["messages"]]
    assert senders[0] == senders[2] and senders[0] != senders[1]
    assert all(item.user_id not in h.platform.runs[0][0] for item in messages)
    assert h.platform.sends[0][2] == messages[-1].message_id
    completed = await h.queue.get_batch(selected.batch_id)
    assert completed.state == "replied" and completed.delivery_id == "delivery-1"
    assert (await h.queue.get_value(h.group.capture_prefix + selected.batch_id))[
        "answer"
    ] == h.platform.output


@pytest.mark.parametrize(
    "case",
    ["unsafe-output", "unknown-output", "oversized-output", "oversized-batch"],
)
async def test_chat_guardrails_end_in_review_without_delivery(harness, case):
    h = harness
    await enqueue(h, "question", "A full question", allowed=True)
    selected = (
        await h.queue.claim_batch(h.group.bot_id, h.group.channel_id, max_chars=3)
        if case == "oversized-batch"
        else await batch(h)
    )
    if case == "unsafe-output":
        h.moderation.decisions[h.platform.output] = decision("remove")
    elif case == "unknown-output":
        h.moderation.decisions[h.platform.output] = decision("review", status="unknown")
    elif case == "oversized-output":
        h.platform.output = "x" * (h.settings.reply_character_limit + 1)
    await h.chat.process(h.group, selected)
    assert not h.platform.sends
    assert (await h.queue.get_batch(selected.batch_id)).state == "review"
    assert await h.queue.get_value(h.group.capture_prefix + selected.batch_id) is None
    if case in {"oversized-batch"}:
        assert not h.platform.runs


async def test_observed_chat_does_not_capture_an_unanswered_discussion(harness):
    h = harness
    await enqueue(h, "discussion", allowed=True)
    selected = await batch(h)
    h.platform.output = " \n\t "
    await h.chat.process(h.group, selected)
    assert len(h.platform.runs) == 1 and not h.platform.sends
    assert (await h.queue.get_batch(selected.batch_id)).state == "observed"
    saved = await h.queue.get_value(h.group.capture_prefix + selected.batch_id)
    assert saved is None


@pytest.mark.parametrize("output", [None, {"respond": True, "text": "do not send"}, ["reply"]])
async def test_non_text_output_cannot_bypass_review_or_reach_the_group(harness, output):
    h = harness
    await enqueue(h, "question", allowed=True)
    selected = await batch(h)
    h.platform.output = output
    with pytest.raises(OpsMeshError) as error:
        await h.chat.process(h.group, selected)
    assert error.value.code == "CONVERSATION_OUTPUT_INVALID"
    assert not h.moderation.reviews and not h.platform.sends
    assert await h.queue.get_value(h.group.capture_prefix + selected.batch_id) is None


async def test_messages_arriving_during_run_are_left_for_next_batch(harness):
    h = harness
    await enqueue(h, "first", "Original question", allowed=True)
    selected = await batch(h)
    started, release = asyncio.Event(), asyncio.Event()

    async def pause():
        started.set()
        await release.wait()

    h.platform.on_run = pause
    task = asyncio.create_task(h.chat.process(h.group, selected))
    try:
        await asyncio.wait_for(started.wait(), 1)
        later = await enqueue(h, "later", "A later question", allowed=True)
        assert await batch(h) is None
        release.set()
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert [row["event_id"] for row in json.loads(h.platform.runs[0][0])["messages"]] == ["first"]
    next_batch = await batch(h)
    assert [item.event_id for item in next_batch.messages] == [later.event_id]
    h.platform.on_run = None
    await h.chat.process(h.group, next_batch)
    assert len(h.platform.delivered) == 2


async def test_recovery_reuses_decision_run_review_and_delivery_identity(harness, monkeypatch):
    h = harness
    await enqueue(h, "recover-chat", allowed=True)
    selected = await batch(h)
    original_set = h.queue.set_value

    async def fail_after_confirmed_delivery(key, value):
        if key == h.group.capture_prefix + selected.batch_id:
            raise OSError("simulated interruption before local finalization")
        await original_set(key, value)

    monkeypatch.setattr(h.queue, "set_value", fail_after_confirmed_delivery)
    with pytest.raises(OpsMeshError) as pending:
        await h.chat.process(h.group, selected)
    assert pending.value.code == "CONVERSATION_COMMIT_PENDING"
    assert len(h.platform.delivered) == 1
    monkeypatch.setattr(h.queue, "set_value", original_set)
    recovered = await h.queue.get_batch(selected.batch_id)
    await h.chat.process(h.group, recovered)
    assert len(h.moderation.reviews) == 1
    assert len(h.platform.run_ledger) == len(h.platform.delivered) == 1
    assert len(h.platform.sends) == 2 and h.platform.sends[0][1] == h.platform.sends[1][1]
    assert h.platform.runs[1][2] == recovered.run_id
    assert (await h.queue.get_batch(selected.batch_id)).state == "replied"


async def test_coordinator_recovers_after_confirmed_send_local_commit_failure(harness, monkeypatch):
    from opsmesh.application.conversations.coordinator import ConversationCoordinator

    h = harness
    await enqueue(h, "commit-interruption", allowed=True)
    coordinator = ConversationCoordinator(
        h.settings.model_copy(update={"batch_delay_seconds": 0}), h.queue, h.platform, h.moderation
    )
    original_set = h.queue.set_value

    async def fail_capture(key, value):
        if key.startswith(h.group.capture_prefix):
            raise OSError("Private disk error")
        await original_set(key, value)

    monkeypatch.setattr(h.queue, "set_value", fail_capture)
    with pytest.raises(OpsMeshError) as pending:
        await coordinator.chat_tick(h.group)
    assert pending.value.code == "CONVERSATION_COMMIT_PENDING"
    selected = (await h.queue.pending_batches())[0]
    assert selected.state == "running" and len(h.platform.delivered) == 1
    await coordinator.chat_tick(h.group)  # Persisted deadline prevents immediate retries.
    assert len(h.platform.sends) == 1
    monkeypatch.setattr(h.queue, "set_value", original_set)
    await h.queue.delete_value("conversation-retry:" + selected.batch_id)
    await coordinator.chat_tick(h.group)
    assert (await h.queue.get_batch(selected.batch_id)).state == "replied"
    assert len(h.platform.run_ledger) == len(h.platform.delivered) == 1
    assert len(h.platform.sends) == 2 and h.platform.sends[0][1] == h.platform.sends[1][1]
    assert len(h.moderation.reviews) == 1


@pytest.mark.parametrize("code", ["BOT_RATE_LIMITED", "BOT_UNAVAILABLE", "BOT_API_FAILED"])
async def test_safe_delivery_rejection_reuses_run_review_and_delivery_key(harness, code):
    from opsmesh.application.conversations.coordinator import ConversationCoordinator

    h = harness
    await enqueue(h, "transient", allowed=True)
    h.platform.send_error = OpsMeshError(code, "safe rejection", retryable=True)
    coordinator = ConversationCoordinator(
        h.settings.model_copy(update={"batch_delay_seconds": 0}), h.queue, h.platform, h.moderation
    )
    with pytest.raises(OpsMeshError):
        await coordinator.chat_tick(h.group)
    selected = (await h.queue.pending_batches())[0]
    await h.queue.delete_value("conversation-retry:" + selected.batch_id)
    await coordinator.chat_tick(h.group)
    assert (await h.queue.get_batch(selected.batch_id)).state == "replied"
    assert len(h.platform.run_ledger) == len(h.platform.delivered) == 1
    assert h.platform.sends[0][1] == h.platform.sends[1][1]
    assert len(h.moderation.reviews) == 1


async def test_unknown_delivery_is_never_automatically_replayed_even_if_marked_retryable(harness):
    from opsmesh.application.conversations.coordinator import ConversationCoordinator

    h = harness
    await enqueue(h, "unknown", allowed=True)
    h.platform.send_error = OpsMeshError("DELIVERY_UNKNOWN", "Uncertain send", retryable=True)
    coordinator = ConversationCoordinator(
        h.settings.model_copy(update={"batch_delay_seconds": 0}), h.queue, h.platform, h.moderation
    )
    await coordinator.chat_tick(h.group)
    await coordinator.chat_tick(h.group)
    assert await h.queue.pending_batches() == ()
    assert (await h.queue.pending_batches(include_failed=True))[0].error_code == "DELIVERY_UNKNOWN"
    assert len(h.platform.sends) == 1
