"""Public conversation lifecycle, protocol neutrality and propagation boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from opsmesh.application.conversations import (
    ConversationApplication,
    ConversationBinding,
    ConversationOptions,
    ReviewDecision,
)
from opsmesh.framework.adapters import BotRef, MessageEvent, OpsMeshMessage, ReplyTarget
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext
from opsmesh.framework.matcher import matchers
from opsmesh.framework.message import handle_event


def options(path):
    return ConversationOptions(
        data_directory=path,
        bindings=(
            ConversationBinding(
                bot_id="adapter-account",
                channel_id="opaque-channel:one",
                principal_id="service-principal",
                agent_ref="single-agent@1",
            ),
        ),
    )


def context(event_id="one", *, channel="opaque-channel:one", message="hello"):
    return DispatchContext(
        event=MessageEvent(
            id=event_id,
            user_id="private-sender",
            session_id=channel,
            message=OpsMeshMessage(message),
        ),
        reply_target=ReplyTarget(
            bot=BotRef(adapter_id="different-provider", bot_id="adapter-account"),
            channel_id=channel,
            message_id="provider-" + event_id,
        ),
    )


async def test_registered_pipeline_intercepts_only_exact_bindings_and_survives_restart(
    tmp_path, monkeypatch, inbox_factory
):
    driver = OpsMeshDriver()
    previous = set(matchers.snapshot())
    policy = SimpleNamespace(review=AsyncMock())
    application = ConversationApplication(
        options(tmp_path),
        policy,
        await inbox_factory(tmp_path / "platform.db"),
        operations=Mock(),
        driver=driver,
    )
    # Isolate ingress and lifecycle; worker behavior has its own real-queue tests.
    application.coordinator.start = Mock()
    try:
        application.register()
        with pytest.raises(OpsMeshError):
            application.register()
        await driver.start()
        assert application.ready.is_set()
        received = context()
        result = await handle_event(received, raise_on_error=True)
        assert result.stopped
        await handle_event(received, raise_on_error=True)
        pending = await application.queue.pending_moderation()
        assert len(pending) == 1 and pending[0].event_id == received.event.id
        assert not (await handle_event(context(channel="other"))).stopped
        policy.review.assert_not_awaited()
        await driver.stop()
        assert not application.ready.is_set()
        with pytest.raises(OpsMeshError):
            await application.receive(received)
        replacement = ConversationApplication(
            options(tmp_path),
            policy,
            await inbox_factory(tmp_path / "platform.db"),
            operations=Mock(),
            driver=driver,
        )
        assert (await replacement.queue.pending_moderation())[0].text == "hello"
        await replacement.close()
    finally:
        await application.close()
        for matcher in set(matchers.snapshot()) - previous:
            matchers.remove(matcher)


async def test_oversized_input_is_retained_for_review_without_partial_model_text(
    tmp_path, inbox_factory
):
    application = ConversationApplication(
        options(tmp_path),
        SimpleNamespace(),
        await inbox_factory(tmp_path / "platform.db"),
        operations=Mock(),
        driver=OpsMeshDriver(),
    )
    application.ready.set()
    try:
        await application.receive(context(message="x" * 16001))
        pending = (await application.queue.pending_moderation())[0]
        assert pending.has_attachments and pending.text == ""
        with pytest.raises(OpsMeshError):
            await application.receive(context(channel="unconfigured"))
    finally:
        await application.close()


def test_unknown_policy_results_cannot_allow_removal_or_reply():
    for action in ("allow", "remove"):
        with pytest.raises(ValidationError):
            ReviewDecision(status="unknown", action=action)


def test_opaque_identifiers_do_not_collide_and_notices_disallow_attribute_interpolation(tmp_path):
    one = ConversationBinding(bot_id="a:b", channel_id="c", principal_id="p", agent_ref="a@1")
    two = one.model_copy(update={"bot_id": "a", "channel_id": "b:c"})
    assert one.key != two.key and one.capture_prefix != two.capture_prefix
    with pytest.raises(ValidationError):
        ConversationOptions(
            data_directory=tmp_path, bindings=(one,), removed_notice="{reason.__class__}"
        )


async def test_close_wakes_an_ingress_waiting_for_startup(tmp_path, inbox_factory):
    application = ConversationApplication(
        options(tmp_path),
        SimpleNamespace(),
        await inbox_factory(tmp_path / "platform.db"),
        operations=Mock(),
        driver=OpsMeshDriver(),
    )
    waiting = asyncio.create_task(application.receive(context()))
    await asyncio.sleep(0)
    assert not waiting.done()
    await application.close()
    with pytest.raises(OpsMeshError):
        await asyncio.wait_for(waiting, timeout=1)


async def test_active_batch_rejects_changed_agent_binding_and_same_binding_resumes(
    tmp_path, monkeypatch, inbox_factory
):
    driver = Mock()
    original = ConversationApplication(
        options(tmp_path),
        SimpleNamespace(),
        await inbox_factory(tmp_path / "platform.db"),
        operations=Mock(),
        driver=driver,
    )
    original.coordinator.start = Mock()
    await original.start()
    await original.receive(context())
    await original.queue.set_moderation("one", {"status": "ok", "action": "allow"}, True)
    binding = original.options.bindings[0]
    batch = await original.queue.claim_batch(binding.bot_id, binding.channel_id)
    await original.queue.record_run(batch.batch_id, "durable-run")
    await original.close()
    updated = original.options.model_copy(
        update={"bindings": (binding.model_copy(update={"agent_ref": "single-agent@2"}),)}
    )
    changed = ConversationApplication(
        updated,
        SimpleNamespace(),
        await inbox_factory(tmp_path / "platform.db"),
        operations=Mock(),
        driver=driver,
    )
    changed.coordinator.start = Mock()
    with pytest.raises(OpsMeshError) as caught:
        await changed.start()
    assert caught.value.code == "CONVERSATION_BINDING_BUSY"
    changed.coordinator.start.assert_not_called()
    assert not changed.ready.is_set()
    replacement = ConversationApplication(
        original.options,
        SimpleNamespace(),
        await inbox_factory(tmp_path / "platform.db"),
        operations=Mock(),
        driver=driver,
    )
    replacement.coordinator.start = Mock()
    await replacement.start()
    assert (await replacement.queue.pending_batches())[0].run_id == "durable-run"
    await replacement.close()
