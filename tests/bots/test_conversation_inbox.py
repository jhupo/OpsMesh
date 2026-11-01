"""Inboxes share the platform database without sharing project state."""

from datetime import UTC, datetime

from opsmesh.modules.bots.conversations import ConversationBatch, QueuedConversationMessage


async def test_scopes_isolate_event_ids_batches_and_coordination_metadata(tmp_path, inbox_factory):
    path = tmp_path / "platform.db"
    first = await inbox_factory(path, namespace="project-one")
    second = await inbox_factory(path, namespace="project-two")
    message = QueuedConversationMessage(
        event_id="same-event",
        bot_id="bot",
        channel_id="opaque/channel:thread",
        user_id="member",
        message_id="provider-message",
        text="Service status?",
        occurred_at=datetime.now(UTC),
        to_me=True,
    )
    assert await first.enqueue(message)
    assert await second.enqueue(message)
    await first.set_moderation(message.event_id, {"status": "ok", "action": "allow"}, True)
    batch = await first.claim_batch(message.bot_id, message.channel_id)
    assert isinstance(batch, ConversationBatch) and batch.messages == (message,)
    assert await second.ready_channels() == ()
    assert await second.pending_moderation() == (message,)
    await first.set_value("session:one", {"session_id": "private-session"})
    assert await second.get_value("session:one") is None
    await first.complete_batch(batch.batch_id, state="observed")
    assert await second.pending_batches() == ()
