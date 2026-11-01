"""Reconfiguration keeps durable work bound to its original resources."""

from datetime import UTC, datetime

import pytest

from opsmesh.application.conversations import ConversationBinding
from opsmesh.application.conversations.reconfiguration import reconfiguration_scope
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.coordination.process_lock import LocalProcessLock
from opsmesh.modules.bots.conversations import QueuedConversationMessage


async def test_apply_rejects_a_running_consumer_without_creating_a_queue(tmp_path, inbox_factory):
    lock = LocalProcessLock(tmp_path / "consumer.lock")
    lock.acquire()
    try:
        with pytest.raises(OpsMeshError) as error:
            async with reconfiguration_scope(
                tmp_path, await inbox_factory(tmp_path / "platform.db")
            ):
                pytest.fail("A running consumer must exclude project publication")
        assert error.value.code == "CONVERSATION_BINDING_BUSY"
        assert not (tmp_path / "queue.sqlite3").exists()
    finally:
        lock.release()
    async with reconfiguration_scope(tmp_path, await inbox_factory(tmp_path / "platform.db")):
        assert not (tmp_path / "queue.sqlite3").exists()


async def test_apply_preserves_an_active_persistent_batch_until_it_is_drained(
    tmp_path, inbox_factory
):
    queue = await inbox_factory(tmp_path / "platform.db")
    await queue.enqueue(
        QueuedConversationMessage(
            event_id="pending-event",
            bot_id="bot",
            channel_id="group:exact",
            user_id="member",
            message_id="message",
            text="Pending work",
            occurred_at=datetime.now(UTC),
            to_me=True,
        )
    )
    await queue.set_moderation(
        "pending-event", {"status": "ok", "action": "allow"}, eligible_for_chat=True
    )
    batch = await queue.claim_batch("bot", "group:exact")
    assert batch is not None
    await queue.record_run(batch.batch_id, "running-agent-run")
    with pytest.raises(OpsMeshError) as error:
        async with reconfiguration_scope(tmp_path, await inbox_factory(tmp_path / "platform.db")):
            pytest.fail("An unfinished batch must retain its existing Agent binding")
    assert error.value.code == "CONVERSATION_BINDING_BUSY"

    reopened = await inbox_factory(tmp_path / "platform.db")
    pending = await reopened.pending_batches()
    assert len(pending) == 1
    assert pending[0].batch_id == batch.batch_id
    assert pending[0].run_id == "running-agent-run"
    await reopened.complete_batch(batch.batch_id, state="observed")
    async with reconfiguration_scope(tmp_path, await inbox_factory(tmp_path / "platform.db")):
        competitor = LocalProcessLock(tmp_path / "consumer.lock")
        with pytest.raises(RuntimeError, match="Another process"):
            competitor.acquire()


async def test_failed_publication_releases_consumer_ownership(tmp_path, inbox_factory):
    with pytest.raises(OpsMeshError, match="Publication failed"):
        async with reconfiguration_scope(tmp_path, await inbox_factory(tmp_path / "platform.db")):
            raise OpsMeshError("CONFLICT", "Publication failed")
    recovered = LocalProcessLock(tmp_path / "consumer.lock")
    recovered.acquire()
    recovered.release()


async def test_apply_keeps_pending_capture_binding_until_knowledge_acknowledges(
    tmp_path, inbox_factory
):
    group = ConversationBinding(
        bot_id="bot",
        channel_id="group:exact",
        principal_id="principal",
        agent_ref="agent@1",
        knowledge_collection="knowledge",
    )
    queue = await inbox_factory(tmp_path / "platform.db")
    await queue.enqueue(
        QueuedConversationMessage(
            event_id="captured-event",
            bot_id="bot",
            channel_id="group:exact",
            user_id="member",
            message_id="message",
            text="Question",
            occurred_at=datetime.now(UTC),
            to_me=True,
        )
    )
    await queue.set_moderation(
        "captured-event", {"status": "ok", "action": "allow"}, eligible_for_chat=True
    )
    batch = await queue.claim_batch("bot", "group:exact")
    assert batch is not None
    await queue.record_run(batch.batch_id, "completed-run")
    await queue.complete_batch(
        batch.batch_id,
        state="replied",
        run_id="completed-run",
        delivery_id="confirmed-delivery",
    )
    capture_key = group.capture_prefix + batch.batch_id
    capture = {
        "batch_id": batch.batch_id,
        "answer": "Approved answer",
        "run_id": "completed-run",
    }
    await queue.set_value(capture_key, capture)
    assert await queue.pending_batches() == ()
    with pytest.raises(OpsMeshError, match="pending knowledge captures") as error:
        async with reconfiguration_scope(tmp_path, await inbox_factory(tmp_path / "platform.db")):
            pytest.fail("Pending capture must be completed under the original binding")
    assert error.value.code == "CONVERSATION_BINDING_BUSY"

    assert await queue.get_value(capture_key) == capture
    assert (await queue.get_batch(batch.batch_id)).delivery_id == "confirmed-delivery"
    await queue.delete_value(capture_key)
    async with reconfiguration_scope(tmp_path, await inbox_factory(tmp_path / "platform.db")):
        pass
