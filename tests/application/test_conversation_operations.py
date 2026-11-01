"""Community platform authorization, durable recovery and delivery boundaries."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from opsmesh.application.conversations import configuration
from opsmesh.application.conversations import operations as platform_module
from opsmesh.framework.adapters import BotRef, ReplyTarget
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.bots.api import DeliveryReceipt
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.sessions.api import ConversationSession

NOW = datetime(2026, 11, 1, tzinfo=UTC)


class RuntimeDouble:
    def __init__(self):
        self.runs = {}
        self.submissions = []
        self.status = "completed"
        self.after_read_status = None
        self.cancel = AsyncMock()

    async def submit(self, **kwargs):
        self.submissions.append(kwargs)
        agent_id, revision = kwargs["agent_ref"].rsplit("@", 1)
        run = AgentRunView(
            id=f"run-{len(self.runs) + 1}",
            revision=1,
            principal_id=kwargs["actor_id"],
            session_id=kwargs["session_id"],
            agent=AgentSpecification(id=agent_id, name=agent_id, revision=int(revision)),
            published_agent_ref=kwargs["agent_ref"],
            status=self.status,
            output="review before sending",
            metadata=kwargs["metadata"],
            overrides=kwargs.get("overrides", {}),
            created_at=NOW,
            updated_at=NOW,
        )
        self.runs[run.id] = run
        return run

    async def get_run(self, run_id, *, actor_id):
        run = self.runs[run_id]
        assert run.principal_id == actor_id
        if self.after_read_status is not None:
            run = run.model_copy(update={"status": self.after_read_status})
            self.runs[run_id] = run
        return run

    async def find_submission(self, idempotency_key, *, actor_id):
        matches = [item for item in self.submissions if item["idempotency_key"] == idempotency_key]
        if not matches:
            return None
        return next(
            run
            for run in self.runs.values()
            if run.principal_id == actor_id and run.metadata == matches[0]["metadata"]
        )


class SessionsDouble:
    def __init__(self):
        self.sessions = {}
        self.keys = {}

    async def open(self, agent_ref, *, actor_id, workspace_id=None):
        session = ConversationSession(
            id=f"session-{len(self.sessions) + 1}",
            owner_id=actor_id,
            agent_ref=agent_ref,
            workspace_id=workspace_id,
            created_at=NOW,
        )
        self.sessions[session.id] = session
        return session

    async def get_or_open(self, key, agent_ref, *, actor_id, workspace_id=None):
        if key not in self.keys:
            self.keys[key] = (
                await self.open(agent_ref, actor_id=actor_id, workspace_id=workspace_id)
            ).id
        return await self.get(self.keys[key], actor_id=actor_id)

    async def get(self, session_id, *, actor_id):
        session = self.sessions[session_id]
        assert session.owner_id == actor_id
        return session


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
    ).model_copy(update={"poll_seconds": 0.001, "run_wait_seconds": 5})
    queue = await inbox_factory(tmp_path / "queue.sqlite3")
    runtime, sessions = RuntimeDouble(), SessionsDouble()

    async def resolve(bot_id, channel_id, *, actor_id, message_id=None):
        assert actor_id == group.principal_id
        return ReplyTarget(
            bot=BotRef(adapter_id="qq", bot_id=bot_id),
            channel_id=channel_id,
            message_id=message_id,
        )

    bots = SimpleNamespace(resolve_delivery_target=AsyncMock(side_effect=resolve))
    delivery = SimpleNamespace(
        send=AsyncMock(return_value=DeliveryReceipt(delivery_id="delivery-1", message_id="qq-1"))
    )
    qq_bot = SimpleNamespace(
        ref=BotRef(adapter_id="qq", bot_id=group.bot_id),
        remove_message=AsyncMock(return_value=None),
    )
    adapter = SimpleNamespace(ensure_enabled=Mock(), bots={group.bot_id: qq_bot})
    driver = SimpleNamespace(adapters={"qq": adapter})

    def factory(options=settings):
        return platform_module.ConversationOperations(
            options,
            queue,
            bots=bots,
            delivery=delivery,
            runtime=runtime,
            sessions=sessions,
            knowledge=Mock(),
            memory=Mock(),
            driver=driver,
        )

    yield SimpleNamespace(
        group=group,
        other=other,
        settings=settings,
        queue=queue,
        runtime=runtime,
        sessions=sessions,
        bots=bots,
        delivery=delivery,
        bot=qq_bot,
        adapter=adapter,
        platform=factory(),
        factory=factory,
    )


async def run(harness, key="batch-1", **kwargs):
    return await harness.platform.run(
        harness.group,
        harness.group.agent_ref,
        "approved conversation",
        key,
        purpose="chat",
        **kwargs,
    )


async def test_submission_records_run_before_waiting_and_never_auto_delivers(harness):
    harness.runtime.status = "running"
    harness.runtime.after_read_status = "completed"
    callback = AsyncMock()
    result = await run(harness, on_submitted=callback)
    callback.assert_awaited_once_with(result.id)
    submission = harness.runtime.submissions[0]
    assert submission["metadata"]["conversation_channel"] == harness.group.channel_id
    assert "reply_target" not in submission["metadata"]
    assert submission["actor_id"] == harness.group.principal_id
    assert len(submission["idempotency_key"]) <= 200
    harness.delivery.send.assert_not_awaited()
    saved = await harness.queue.list_values("conversation:run:")
    assert saved[0][1]["run_id"] == result.id


async def test_visual_analysis_uses_isolated_session_and_disables_tools(harness):
    h = harness
    chat = await run(h)
    image_input = [
        {
            "role": "user",
            "content": [{"type": "input_image", "image_url": "https://cdn.example/image.png"}],
        }
    ]
    visual = await h.platform.run(
        h.group, h.group.agent_ref, image_input, "image-1", purpose="media"
    )
    replay = await h.platform.run(
        h.group, h.group.agent_ref, image_input, "image-1", purpose="media"
    )
    other = await h.platform.run(
        h.group, h.group.agent_ref, image_input, "image-2", purpose="media"
    )
    assert visual.id == replay.id
    assert len({chat.session_id, visual.session_id, other.session_id}) == 3
    assert len(h.runtime.submissions) == 3
    submission = h.runtime.submissions[1]
    assert submission["input"] == image_input
    assert submission["agent_ref"] == h.group.agent_ref
    assert submission["overrides"].tool_choice == "none"
    assert "reply_target" not in submission["metadata"]
    h.delivery.send.assert_not_awaited()


async def test_restart_recovers_same_run_even_if_new_context_changes_prompt(harness):
    original = await run(harness)
    harness.platform = harness.factory(harness.settings)
    recovered = await harness.platform.run(
        harness.group, "chat@1", "later context changed", "batch-1", purpose="chat"
    )
    assert recovered.id == original.id
    assert len(harness.runtime.submissions) == 1
    assert len(harness.sessions.sessions) == 1


async def test_crash_after_submission_before_id_saved_recovers_by_operation(harness, monkeypatch):
    original_set = harness.queue.set_value

    async def fail_after_submit(key, value):
        if key.startswith("conversation:run:") and "run_id" in value:
            raise OSError("simulated persistence failure")
        await original_set(key, value)

    monkeypatch.setattr(harness.queue, "set_value", fail_after_submit)
    with pytest.raises(OSError):
        await run(harness)
    monkeypatch.setattr(harness.queue, "set_value", original_set)
    harness.platform = harness.factory(harness.settings)
    result = await run(harness)
    assert result.id == "run-1"
    assert len(harness.runtime.submissions) == 1


async def test_chat_and_activity_share_group_session_with_separate_run_keys(harness):
    await run(harness)
    await run(harness, key="batch-2")
    await harness.platform.run(harness.other, "chat@1", "safe", "batch-1", purpose="chat")
    await harness.platform.run(harness.group, "chat@1", "safe", "batch-1", purpose="proactive")
    assert len(harness.sessions.sessions) == 2
    assert len({item["idempotency_key"] for item in harness.runtime.submissions}) == 4


async def test_concurrent_duplicate_submission_opens_one_session_and_one_run(harness):
    results = await asyncio.gather(*(run(harness) for _ in range(6)))
    assert len({result.id for result in results}) == 1
    assert len(harness.runtime.submissions) == 1
    assert len(harness.sessions.sessions) == 1


async def test_session_uses_bound_workspace_and_rejects_changed_workspace(harness):
    harness.group = harness.group.model_copy(update={"workspace_id": "community-workspace"})
    harness.settings = harness.settings.model_copy(
        update={"bindings": (harness.group, harness.other)}
    )
    harness.platform = harness.factory(harness.settings)
    result = await run(harness)
    session = harness.sessions.sessions[result.session_id]
    assert session.workspace_id == "community-workspace"
    harness.sessions.sessions[result.session_id] = session.model_copy(
        update={"workspace_id": "another-workspace"}
    )
    with pytest.raises(OpsMeshError, match="different workspace"):
        await run(harness, key="another-batch")


@pytest.mark.parametrize("status", ["failed", "blocked", "cancelled", "waiting_approval"])
async def test_unsuccessful_run_never_returns_output_as_success(harness, status):
    harness.runtime.status = status
    with pytest.raises(OpsMeshError) as caught:
        await run(harness)
    assert caught.value.code == "CONVERSATION_RUN_" + status.upper()
    assert caught.value.details["run_id"] == "run-1"
    assert caught.value.retryable == (status == "waiting_approval")
    harness.delivery.send.assert_not_awaited()


async def test_wait_timeout_keeps_run_and_recovery_does_not_resubmit(harness):
    harness.platform.settings = harness.settings.model_copy(update={"run_wait_seconds": 0.05})
    harness.runtime.status = "running"
    with pytest.raises(OpsMeshError) as caught:
        await run(harness)
    assert caught.value.code == "CONVERSATION_RUN_TIMEOUT"
    assert caught.value.retryable
    harness.runtime.cancel.assert_not_awaited()
    harness.runtime.after_read_status = "completed"
    assert (await run(harness)).id == "run-1"
    assert len(harness.runtime.submissions) == 1


async def test_cancellation_of_waiter_keeps_durable_run(harness):
    harness.runtime.status = "running"
    submitted = asyncio.Event()

    async def record(_run_id):
        submitted.set()

    task = asyncio.create_task(run(harness, on_submitted=record))
    await submitted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    harness.runtime.cancel.assert_not_awaited()
    assert len(harness.runtime.submissions) == 1


async def test_binding_agent_and_recovered_run_are_verified(harness):
    forged = harness.group.model_copy(update={"principal_id": "administrator"})
    with pytest.raises(OpsMeshError, match="exact group binding"):
        await harness.platform.run(forged, "chat@1", "safe", "batch", purpose="chat")
    with pytest.raises(OpsMeshError, match="Agent and purpose"):
        await harness.platform.run(harness.group, "foreign@1", "safe", "batch", purpose="chat")
    original = await run(harness)
    with pytest.raises(OpsMeshError, match="reviewed conversation workflow"):
        await harness.platform.run(
            harness.other, "chat@1", "safe", "batch-1", purpose="chat", existing_run_id=original.id
        )
    tampered = original.model_copy(update={"metadata": dict(original.metadata, reply_target={})})
    harness.runtime.runs[original.id] = tampered
    with pytest.raises(OpsMeshError, match="reviewed conversation workflow"):
        await run(harness)


async def test_send_uses_authorized_target_and_scoped_stable_delivery_key(harness):
    first = await harness.platform.send(
        harness.group, "reviewed reply", "reply-1", message_id="m-1"
    )
    await harness.platform.send(harness.group, "reviewed reply", "reply-1", message_id="m-1")
    calls = harness.delivery.send.await_args_list
    assert calls[0].kwargs["idempotency_key"] == calls[1].kwargs["idempotency_key"]
    assert calls[0].kwargs["actor_id"] == harness.group.principal_id
    assert calls[0].args[0].message_id == "m-1"
    assert str(calls[0].args[1]) == "reviewed reply"
    assert first.delivery_id == "delivery-1"
    await harness.platform.send(harness.other, "reviewed reply", "reply-1")
    assert (
        harness.delivery.send.await_args.kwargs["idempotency_key"]
        != calls[0].kwargs["idempotency_key"]
    )


async def test_recall_is_authorized_and_targets_only_configured_group(harness):
    await harness.platform.recall(harness.group, "message-1")
    harness.bots.resolve_delivery_target.assert_awaited_once()
    harness.adapter.ensure_enabled.assert_called_once()
    harness.bot.remove_message.assert_awaited_once_with(
        ReplyTarget(
            bot=harness.bot.ref, channel_id=harness.group.channel_id, message_id="message-1"
        )
    )
    error = OpsMeshError("FORBIDDEN", "QQ recall denied", provider_code="40062003")
    harness.bot.remove_message.side_effect = error
    with pytest.raises(OpsMeshError) as caught:
        await harness.platform.recall(harness.group, "message-1")
    assert caught.value is error


async def test_delivery_authorization_failure_prevents_protocol_side_effect(harness):
    harness.bots.resolve_delivery_target.side_effect = OpsMeshError("FORBIDDEN", "Denied")
    with pytest.raises(OpsMeshError):
        await harness.platform.recall(harness.group, "message-1")
    with pytest.raises(OpsMeshError):
        await harness.platform.send(harness.group, "reviewed", "reply-1")
    harness.bot.remove_message.assert_not_awaited()
    harness.delivery.send.assert_not_awaited()


async def test_capture_is_idempotent_raw_source_without_user_identifiers(harness, monkeypatch):
    import json

    from opsmesh.modules.bots.conversations import QueuedConversationMessage

    message = QueuedConversationMessage(
        event_id="event-1",
        bot_id=harness.group.bot_id,
        channel_id=harness.group.channel_id,
        user_id="private-user-id",
        message_id="provider-message",
        text="Original question",
        occurred_at=NOW,
        to_me=True,
    )
    await harness.queue.enqueue(message)
    await harness.queue.set_moderation(message.event_id, {"status": "ok", "action": "allow"}, True)
    batch = await harness.queue.claim_batch(harness.group.bot_id, harness.group.channel_id)
    documents = {}

    async def add(document, *, principal_id):
        assert principal_id == harness.group.principal_id
        if document.id in documents:
            raise OpsMeshError("CONFLICT", "Already exists")
        documents[document.id] = document

    async def get(document_id, *, principal_id):
        assert principal_id == harness.group.principal_id
        return documents[document_id]

    knowledge = SimpleNamespace(
        add=AsyncMock(side_effect=add), get_document=AsyncMock(side_effect=get)
    )
    harness.platform.knowledge = knowledge
    with pytest.raises(OpsMeshError):
        await harness.platform.capture(harness.group, batch, "Answer", "run-one")
    knowledge.add.assert_not_awaited()
    await harness.queue.record_run(batch.batch_id, "run-one")
    await harness.queue.complete_batch(
        batch.batch_id, state="replied", run_id="run-one", delivery_id="confirmed-send"
    )
    completed = await harness.queue.get_batch(batch.batch_id)
    memory = SimpleNamespace(append_source=AsyncMock(), request_generation=AsyncMock())
    harness.platform.memory = memory
    group = harness.group.model_copy(update={"memory_namespace_id": "group-memory"})
    harness.platform.settings = harness.settings.model_copy(update={"bindings": (group,)})
    await harness.platform.capture(group, completed, "Answer", "run-one")
    await harness.platform.capture(group, completed, "Answer", "run-one")
    sources = [call.args[0] for call in memory.append_source.await_args_list]
    assert sources[0] == sources[1]
    assert sources[0].namespace_id == "group-memory"
    assert sources[0].text == next(iter(documents.values())).text
    assert sources[0].conversation_id == group.key
    assert (
        memory.request_generation.await_args_list[0] == memory.request_generation.await_args_list[1]
    )
    assert len(documents) == 1
    document = next(iter(documents.values()))
    payload = json.loads(document.text)
    assert payload == {
        "record_type": "raw_conversation",
        "authoritative": False,
        "source_event_ids": ["event-1"],
        "questions": ["Original question"],
        "delivered_answer": "Answer",
    }
    assert "private-user-id" not in document.text
    with pytest.raises(OpsMeshError, match="changed after delivery"):
        await harness.platform.capture(group, completed, "Changed answer", "run-one")


async def test_approval_resumes_original_batch_once_and_keeps_apply_blocked(harness):
    from opsmesh.application.conversations import ReviewDecision
    from opsmesh.application.conversations.coordinator import ConversationCoordinator
    from opsmesh.application.conversations.reconfiguration import (
        reconfiguration_scope as _apply_scope,
    )
    from opsmesh.modules.bots.conversations import QueuedConversationMessage

    group = harness.group
    await harness.queue.enqueue(
        QueuedConversationMessage(
            event_id="approval-event",
            bot_id=group.bot_id,
            channel_id=group.channel_id,
            user_id="member",
            message_id="provider-id",
            text="Approved question",
            occurred_at=NOW,
            to_me=True,
        )
    )
    await harness.queue.set_moderation("approval-event", {"status": "ok", "action": "allow"}, True)
    policy = SimpleNamespace(
        review=AsyncMock(return_value=ReviewDecision(status="ok", action="allow")),
    )
    coordinator = ConversationCoordinator(
        harness.settings.model_copy(update={"batch_delay_seconds": 0}),
        harness.queue,
        harness.platform,
        policy,
    )
    harness.runtime.status = "waiting_approval"
    with pytest.raises(OpsMeshError) as waiting:
        await coordinator.chat_tick(group)
    assert waiting.value.code == "CONVERSATION_RUN_WAITING_APPROVAL"
    selected = (await harness.queue.pending_batches())[0]
    assert selected.state == "running" and selected.run_id == "run-1"
    assert selected.error_code == waiting.value.code
    policy.review.assert_not_awaited()
    harness.delivery.send.assert_not_awaited()
    with pytest.raises(OpsMeshError) as busy:
        async with _apply_scope(harness.settings.data_directory, harness.queue):
            pytest.fail("Pending approval must prevent apply")
    assert busy.value.code == "CONVERSATION_BINDING_BUSY"
    # Restart preserves the poll deadline and the original Run binding.
    replacement = ConversationCoordinator(
        coordinator.settings, harness.queue, harness.platform, policy
    )
    await replacement.chat_tick(group)
    assert len(harness.runtime.submissions) == 1
    harness.runtime.runs["run-1"] = harness.runtime.runs["run-1"].model_copy(
        update={"status": "completed"}
    )
    await harness.queue.delete_value("conversation-retry:" + selected.batch_id)
    await replacement.chat_tick(group)
    await replacement.chat_tick(group)
    completed = await harness.queue.get_batch(selected.batch_id)
    assert completed.state == "replied" and completed.run_id == "run-1"
    assert len(harness.runtime.submissions) == 1
    policy.review.assert_awaited_once()
    harness.delivery.send.assert_awaited_once()
    harness.runtime.cancel.assert_not_awaited()
