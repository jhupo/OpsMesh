"""Image evidence follows durable Runs, moderation and existing conversation delivery."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from opsmesh.application.conversations.chat import ConversationChatWorker
from opsmesh.application.conversations.configuration import ConversationBinding, ConversationOptions
from opsmesh.application.conversations.contracts import ReviewDecision
from opsmesh.application.conversations.media import ConversationMedia, media_key, retain_attachments
from opsmesh.application.conversations.moderation import ConversationModerationWorker
from opsmesh.framework.adapters import OpsMeshMessageSegment
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.conversations import QueuedConversationMessage


@pytest_asyncio.fixture
async def h(tmp_path, inbox_factory):
    group = ConversationBinding(
        bot_id="qq-bot", channel_id="group:one", principal_id="member", agent_ref="assistant@1"
    )
    settings = ConversationOptions(
        data_directory=tmp_path,
        bindings=(group,),
        images_enabled=True,
        image_allowed_hosts=("cdn.example",),
    )
    inbox = await inbox_factory(tmp_path / "media.db")

    async def run(_group, agent_ref, _prompt, key, *, purpose, **kwargs):
        assert _group == group and agent_ref == group.agent_ref
        if kwargs.get("on_submitted"):
            await kwargs["on_submitted"]("chat-run")
        return SimpleNamespace(
            id="visual-run" if purpose == "media" else "chat-run",
            output="A red square with visible text: hello."
            if purpose == "media"
            else "It is a red square.",
        )

    platform = SimpleNamespace(
        run=AsyncMock(side_effect=run),
        send=AsyncMock(return_value=SimpleNamespace(delivery_id="sent")),
        recall=AsyncMock(),
    )
    moderation = SimpleNamespace(
        review=AsyncMock(
            return_value=ReviewDecision(
                status="ok", action="allow", reason="allowed", reason_code="allowed"
            )
        )
    )
    return SimpleNamespace(
        group=group,
        settings=settings,
        queue=inbox,
        platform=platform,
        moderation=moderation,
        worker=ConversationModerationWorker(settings, inbox, platform, moderation),
        media=ConversationMedia(settings, inbox, platform),
    )


async def image(h, event_id="image-1", *, segments=None):
    attachments = segments or (OpsMeshMessageSegment.image("https://cdn.example/picture.png"),)
    message = QueuedConversationMessage(
        event_id=event_id,
        bot_id=h.group.bot_id,
        channel_id=h.group.channel_id,
        user_id="sender",
        message_id="provider-" + event_id,
        text="What is this?",
        occurred_at=datetime.now(UTC),
        to_me=False,
        has_attachments=True,
    )
    await retain_attachments(h.queue, event_id, message.bot_id, message.channel_id, attachments)
    await h.queue.enqueue(message)
    return message


async def test_image_evidence_is_reviewed_then_used_by_same_chat_agent(h):
    message = await image(h)
    await h.worker.media_tick()
    reviewed = h.moderation.review.call_args.args[0]
    assert "red square" in reviewed and "https://" not in reviewed
    await h.worker.review(message)
    assert h.platform.run.await_count == 1
    batch = await h.queue.claim_batch(h.group.bot_id, h.group.channel_id)
    await ConversationChatWorker(h.settings, h.queue, h.platform, h.moderation).process(
        h.group, batch
    )
    call = h.platform.run.call_args
    assert call.kwargs["purpose"] == "chat"
    assert "red square" in json.loads(call.args[2])["messages"][0]["text"]
    h.platform.send.assert_awaited_once()


async def test_slow_image_does_not_block_text_moderation(h):
    picture = await image(h)
    text = QueuedConversationMessage(
        event_id="ordinary",
        bot_id=h.group.bot_id,
        channel_id=h.group.channel_id,
        user_id="sender",
        message_id="provider-text",
        text="hello",
        occurred_at=datetime.now(UTC),
        to_me=False,
    )
    await h.queue.enqueue(text)
    started, release = asyncio.Event(), asyncio.Event()
    original = h.platform.run.side_effect

    async def slow(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    h.platform.run.side_effect = slow
    task = asyncio.create_task(h.worker.media_tick())
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(h.worker.tick(), 1)
        assert [item.event_id for item in await h.queue.pending_moderation()] == [picture.event_id]
        assert h.moderation.review.call_args.args[0] == "hello"
    finally:
        release.set()
        await task


@pytest.mark.parametrize(
    "segment",
    [
        OpsMeshMessageSegment.image("http://cdn.example/x"),
        OpsMeshMessageSegment.image("https://127.0.0.1/x"),
        OpsMeshMessageSegment.image("https://cdn.example.attacker.test/x"),
        OpsMeshMessageSegment.audio("https://cdn.example/x"),
    ],
)
async def test_unsupported_attachment_never_reaches_model_or_chat(h, segment):
    message = await image(h, segments=(segment,))
    await h.worker.media_tick()
    saved = await h.queue.get_value("moderation:" + message.event_id)
    assert saved["decision"]["action"] == "review"
    h.platform.run.assert_not_awaited()
    h.moderation.review.assert_not_awaited()
    assert await h.queue.ready_channels() == ()


async def test_duplicate_attachment_retains_analysis_and_rejects_changed_content(h):
    message = await image(h)
    first = await h.media.prepare(h.group, message)
    await image(h)
    assert await h.media.prepare(h.group, message) == first
    h.platform.run.assert_awaited_once()
    with pytest.raises(OpsMeshError, match="identity changed"):
        await image(h, segments=(OpsMeshMessageSegment.image("https://cdn.example/other.png"),))
    other = h.group.model_copy(update={"channel_id": "group:other"})
    with pytest.raises(OpsMeshError, match="another conversation"):
        await h.media.prepare(other, replace(message, channel_id=other.channel_id))


async def test_terminal_analysis_failure_is_not_resubmitted(h):
    message = await image(h)
    h.platform.run.side_effect = OpsMeshError("CONVERSATION_RUN_FAILED", "failed")
    await h.worker.media_tick()
    await h.worker.review(message)
    h.platform.run.assert_awaited_once()
    assert await h.queue.pending_moderation() == ()
    assert await h.queue.ready_channels() == ()


async def test_attachment_retention_keeps_pending_input_and_cleans_purged_input(h):
    message = await image(h)
    key = media_key(message.event_id)
    record = await h.queue.get_value(key)
    record["saved_at"] = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    await h.queue.set_value(key, record)
    cutoff = datetime.now(UTC) + timedelta(seconds=10)
    await h.media.purge(cutoff)
    assert await h.queue.get_value(key) is not None
    await h.worker.media_tick()
    batch = await h.queue.claim_batch(h.group.bot_id, h.group.channel_id)
    await h.queue.complete_batch(batch.batch_id, state="observed")
    await h.queue.purge_before(cutoff)
    assert not await h.queue.has_message(message.event_id)
    await h.media.purge(cutoff)
    assert await h.queue.get_value(key) is None
    assert await h.queue.get_value(key + ":analysis") is None
