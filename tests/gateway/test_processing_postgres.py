"""Durable gateway behavior against an explicitly configured disposable database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from opsmesh_sdk.channel_management import ConversationRef, RecallMessage
from opsmesh_sdk.channels import ContentPart, InboundMessage
from opsmesh_sdk.messages import MessageFilterResult
from sqlalchemy import event, func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import OperationalError

from opsmesh.gateway.channels.leases import ChannelLeases
from opsmesh.gateway.channels.policy import (
    GatewayPolicy,
    GroupPolicy,
    ManagementGrant,
    ManagementPolicy,
)
from opsmesh.gateway.channels.registry import ChannelRegistry
from opsmesh.gateway.contracts import (
    ChannelDefinition,
    ChannelTarget,
    DeliveryResolution,
    GatewayEvent,
    GatewayEvents,
    PushCreate,
)
from opsmesh.gateway.identity.identities import Identities
from opsmesh.gateway.management.contracts import OperationOrigin
from opsmesh.gateway.management.service import Management
from opsmesh.gateway.management.worker import ManagementWorker
from opsmesh.gateway.messaging import push
from opsmesh.gateway.messaging.dispatcher import Dispatcher
from opsmesh.gateway.messaging.filters import FilterDecision
from opsmesh.gateway.messaging.inbox import Inbox
from opsmesh.gateway.messaging.outbox import Outbox
from opsmesh.gateway.messaging.recovery import resolve_delivery, retry_inbox
from opsmesh.gateway.routing.service import channel_target
from opsmesh.gateway.storage.models import (
    GatewayAccountBinding,
    GatewayDelivery,
    GatewayInbox,
    GatewayManagementOperation,
)
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.service import create_user


@pytest.fixture
def gateway(database_sessions):
    with database_sessions.begin() as session:
        user = create_user(session, email="gateway@example.com", display_name="Gateway admin")
        user_id = user.id
    store = GatewayStore(database_sessions, database_sessions.kw["info"]["dispatch_cipher"])
    definition = store.save(
        ChannelDefinition(
            id=uuid4(),
            owner_id=user_id,
            name="Acceptance",
            kind="qq",
            enabled=True,
            target=ChannelTarget(user_id=user_id, target_kind="agent", target_id=uuid4()),
            policy=GatewayPolicy(
                message_filters=["review/message_filter/check"],
                group=GroupPolicy(),
                groups={
                    key: GroupPolicy(
                        management=ManagementPolicy(
                            actions=frozenset({"recall", "mute", "remove_member"}),
                            grants={
                                "review/message_filter/check": ManagementGrant(
                                    actions=frozenset({"recall", "mute", "remove_member"}),
                                    automatic=True,
                                )
                            },
                        )
                    )
                    for key in ("group", "shared", "shared-group")
                },
                debounce_seconds=0.1,
                progress_after_seconds=1,
                typing_interval_seconds=1,
                streaming=True,
            ),
        ),
        user_id,
    )
    owner = "acceptance-worker"
    assert ChannelLeases(store).acquire(definition.id, owner)
    inbox, outbox = Inbox(store), Outbox(store, Management(store, ChannelRegistry().generation))
    identities = Identities(store)
    counter = 0

    def receive(
        event,
        text="Hello",
        *,
        sender="alice",
        chat="private",
        group=False,
        mentioned=True,
        approve=True,
        role="member",
        channel_revision=None,
    ):
        nonlocal counter
        message = InboundMessage.model_validate(
            {
                "event_id": event,
                "occurred_at": datetime.now(UTC).isoformat(),
                "actor": {
                    "scope": "app",
                    "id_kind": "member_openid" if group else "user_openid",
                    "external_id": sender,
                    "external_role": role,
                },
                "conversation": {"external_id": chat, "kind": "group" if group else "private"},
                "context": {"bot_mentioned": mentioned, "visibility": "all"},
                "content": [{"text": text}],
                "reply_address": {
                    "kind": "group" if group else "c2c",
                    "target": chat,
                    "msg_id": event,
                },
            }
        )
        inbox.receive(
            definition.id,
            owner,
            message,
            channel_revision=(
                store.get(definition.id).revision if channel_revision is None else channel_revision
            ),
        )
        with database_sessions.begin() as session:
            row = session.scalar(
                select(GatewayInbox).where(
                    GatewayInbox.channel_id == definition.id,
                    GatewayInbox.event_key == payload_digest({"scope": "app", "event": event}),
                )
            )
            counter += 1
            row.created_at = datetime.now(UTC) - timedelta(seconds=10 - counter / 100)
            identifier = row.id
        turn, _ = inbox.turn(identifier, owner)
        if approve and turn.status == "pending":
            inbox.approved(turn, MessageFilterResult(action="allow"))
        return identifier

    return SimpleNamespace(
        store=store,
        sessions=database_sessions,
        channel=definition,
        owner=owner,
        user_id=user_id,
        inbox=inbox,
        outbox=outbox,
        receive=receive,
        identities=identities,
    )


def test_debounce_waits_for_quiet_and_preserves_batch_after_recovery(gateway, monkeypatch):
    first = gateway.receive("first", "Part one")
    second = gateway.receive("second", "Part two")
    control = gateway.receive("cancel", "/cancel")
    now = datetime.now(UTC)
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    monkeypatch.setattr("opsmesh.gateway.messaging.inbox.datetime", clock)
    with gateway.sessions.begin() as session:
        session.get(GatewayInbox, first).created_at = now - timedelta(seconds=0.05)
        session.get(GatewayInbox, second).created_at = now
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    assert gateway.inbox.claim(turn) is None
    assert control in gateway.inbox.pending(gateway.channel.id)
    with gateway.sessions.begin() as session:
        session.get(GatewayInbox, first).created_at = now - timedelta(seconds=1)
        session.get(GatewayInbox, second).created_at = now - timedelta(seconds=0.9)
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    turn = gateway.inbox.claim(turn)
    assert [item.message.content[0].text for item in turn.batch] == ["Part one", "Part two"]
    assert turn.message.content[0].text == "Part one"
    with gateway.sessions() as session:
        assert session.get(GatewayInbox, second).status == "batched"
        assert session.get(GatewayInbox, control).status == "ready"
    restored, _ = Inbox(gateway.store).turn(first, gateway.owner)
    assert restored.batch == turn.batch


def test_fifo_keeps_control_interruptible_while_a_run_is_active(gateway):
    # Disable batching to verify the independent per-conversation FIFO contract.
    definition = gateway.store.get(gateway.channel.id)
    gateway.store.save(
        definition.model_copy(
            update={"policy": definition.policy.model_copy(update={"debounce_seconds": 0})}
        ),
        gateway.user_id,
    )
    first = gateway.receive("first")
    second = gateway.receive("second")
    control = gateway.receive("cancel", "/cancel")
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    gateway.inbox.submitted(turn, uuid4())
    pending = gateway.inbox.pending(gateway.channel.id)
    assert first in pending and control in pending
    second_turn, _ = gateway.inbox.turn(second, gateway.owner)
    assert gateway.inbox.claim(second_turn) is None
    gateway.outbox.complete(turn, [ContentPart(text="Done")])
    assert gateway.inbox.claim(second_turn) is not None


def test_duplicate_receipt_and_lost_lease_do_not_add_messages(gateway):
    gateway.receive("duplicate")
    gateway.receive("duplicate")
    with gateway.sessions() as session:
        assert session.scalar(select(func.count()).select_from(GatewayInbox)) == 1
        assert (
            session.scalar(
                select(AuditEvent.source).where(
                    AuditEvent.category == "gateway", AuditEvent.action == "channel.save"
                )
            )
            == "platform"
        )
    with pytest.raises(ConflictError, match="lease"):
        gateway.inbox.turn(gateway.inbox.pending(gateway.channel.id)[0], "another-worker")


def test_final_reply_discards_only_unsent_hints_and_typing(gateway):
    turn, _ = gateway.inbox.turn(gateway.receive("first"), gateway.owner)
    gateway.inbox.submitted(turn, uuid4())
    gateway.outbox.progress(turn)
    gateway.outbox.complete(turn, [ContentPart(text="Final")])
    with gateway.sessions() as session:
        rows = session.scalars(select(GatewayDelivery)).all()
        assert {(row.kind, row.status) for row in rows} == {
            ("hint", "discarded"),
            ("typing", "discarded"),
            ("message", "pending"),
        }
    assert gateway.outbox.next(gateway.channel.id, gateway.owner).content[0].text == "Final"


def test_uncertain_delivery_blocks_later_parts_until_explicit_acknowledgement(gateway):
    turn, _ = gateway.inbox.turn(gateway.receive("first"), gateway.owner)
    gateway.outbox.complete(turn, [ContentPart(text="One"), ContentPart(text="Two")])
    first = gateway.outbox.next(gateway.channel.id, gateway.owner)
    gateway.outbox.finish(gateway.channel.id, gateway.owner, first.delivery_id, "uncertain")
    assert gateway.outbox.next(gateway.channel.id, gateway.owner) is None
    with pytest.raises(InvalidArgumentError, match="duplicate"):
        resolve_delivery(
            gateway.store,
            gateway.channel.id,
            first.delivery_id,
            gateway.user_id,
            DeliveryResolution(action="retry"),
        )
    resolve_delivery(
        gateway.store,
        gateway.channel.id,
        first.delivery_id,
        gateway.user_id,
        DeliveryResolution(action="acknowledge", remote_id="verified"),
    )
    assert gateway.outbox.next(gateway.channel.id, gateway.owner).content[0].text == "Two"


def test_send_limit_survives_outbox_recreation(gateway):
    turn, _ = gateway.inbox.turn(gateway.receive("first"), gateway.owner)
    gateway.outbox.complete(turn, [ContentPart(text="One"), ContentPart(text="Two")])
    first = gateway.outbox.next(gateway.channel.id, gateway.owner, send_limit=1)
    gateway.outbox.finish(gateway.channel.id, gateway.owner, first.delivery_id, "sent", "remote")
    assert Outbox(gateway.store).next(gateway.channel.id, gateway.owner, send_limit=1) is None


def test_stream_frames_coalesce_and_keep_confirmed_platform_prefix(gateway):
    turn, _ = gateway.inbox.turn(gateway.receive("first"), gateway.owner)
    gateway.inbox.submitted(turn, uuid4())
    gateway.outbox.events(
        turn,
        GatewayEvents(
            cursor=1, events=[GatewayEvent(sequence=1, kind="delta", text="Hello first")]
        ),
    )
    first = gateway.outbox.next(gateway.channel.id, gateway.owner)
    assert first.event == "delta" and first.stream_index == 0
    gateway.outbox.finish(gateway.channel.id, gateway.owner, first.delivery_id, "sent", "stream")
    gateway.outbox.events(
        turn,
        GatewayEvents(cursor=2, events=[GatewayEvent(sequence=2, kind="delta", text=" extended")]),
    )
    gateway.outbox.complete(turn, [ContentPart(text="Hello second")])
    final = gateway.outbox.next(gateway.channel.id, gateway.owner)
    assert final.event == "completed" and final.stream_index == 1
    assert final.address["stream_msg_id"] == "stream"
    assert final.content[0].text == "Hello firstsecond"


def test_broadcast_deduplicates_group_destinations_and_is_idempotent(gateway):
    for sender in ("alice", "bob"):
        turn, _ = gateway.inbox.turn(
            gateway.receive(
                sender, sender=sender, chat="shared-group", group=True, mentioned=False
            ),
            gateway.owner,
        )
        gateway.inbox.ignore(turn)
    turn, _ = gateway.inbox.turn(gateway.receive("private"), gateway.owner)
    gateway.outbox.complete(turn, [ContentPart(text="Done")])
    body = PushCreate(request_id=uuid4(), broadcast=True, content=[ContentPart(text="Notice")])
    targets = push.recipients(gateway.store, gateway.channel.id, body)
    assert len(targets) == 2
    first = push.enqueue(
        gateway.store, gateway.channel.id, gateway.user_id, body, targets, body.content
    )
    assert len(first) == 2
    assert (
        push.enqueue(
            gateway.store, gateway.channel.id, gateway.user_id, body, targets, body.content
        )
        == first
    )
    changed = body.model_copy(update={"content": [ContentPart(text="Changed")]})
    with pytest.raises(ConflictError):
        push.enqueue(
            gateway.store, gateway.channel.id, gateway.user_id, changed, targets, changed.content
        )


def test_group_batch_preserves_senders_without_requiring_account_bindings(gateway):
    first = gateway.receive("alice", chat="group", group=True, role="admin")
    second = gateway.receive("bob", sender="bob", chat="group", group=True)
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    batch = gateway.inbox.claim(turn)
    assert [item.inbox_id for item in batch.batch] == [first, second]
    assert [item.message.actor.external_id for item in batch.batch] == ["alice", "bob"]
    assert batch.source.external_role is None
    assert batch.source.identity_id is None
    assert len(batch.source.participants) == 2
    assert batch.source.session_scope == turn.source.session_scope
    gateway.identities.unbind(batch.batch[1].identity_id, gateway.user_id)
    restored, _ = gateway.inbox.turn(first, gateway.owner)
    assert restored.batch == batch.batch


def test_unadmitted_message_cannot_enter_or_be_skipped_by_a_batch(gateway):
    first = gateway.receive("first", chat="group", group=True)
    waiting = gateway.receive("waiting", sender="bob", chat="group", group=True, approve=False)
    third = gateway.receive("third", chat="group", group=True)
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    batch = gateway.inbox.claim(turn)
    assert [item.inbox_id for item in batch.batch] == [first]
    gateway.outbox.complete(batch, [])
    turn, _ = gateway.inbox.turn(third, gateway.owner)
    assert gateway.inbox.claim(turn) is None
    turn, _ = gateway.inbox.turn(waiting, gateway.owner)
    gateway.inbox.approved(turn, MessageFilterResult(action="allow"))
    batch = gateway.inbox.claim(turn)
    assert [item.inbox_id for item in batch.batch] == [waiting, third]


def test_new_message_waits_for_active_group_run_then_forms_next_batch(gateway):
    first = gateway.receive("first", chat="group", group=True)
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    active = gateway.inbox.claim(turn)
    gateway.inbox.submitted(active, uuid4())
    second = gateway.receive("second", sender="bob", chat="group", group=True)
    third = gateway.receive("third", chat="group", group=True)
    turn, _ = gateway.inbox.turn(second, gateway.owner)
    assert gateway.inbox.claim(turn) is None
    gateway.outbox.complete(active, [ContentPart(text="Done")])
    batch = gateway.inbox.claim(turn)
    assert [item.inbox_id for item in batch.batch] == [second, third]


@pytest.mark.asyncio
async def test_moderation_recalls_only_rejected_original_message(gateway, tmp_path):
    good = gateway.receive("good", chat="group", group=True)
    bad = gateway.receive("bad", chat="group", group=True, approve=False)
    current, _ = gateway.inbox.turn(bad, gateway.owner)
    decision = recall_decision(current)
    filters = Mock(evaluate=AsyncMock(return_value=decision))
    execution = Mock(authorize=AsyncMock(), submit=AsyncMock())
    dispatcher = Dispatcher(gateway.inbox, gateway.outbox, execution, tmp_path, filters)
    await dispatcher.process(bad, gateway.owner)
    filters.evaluate.assert_awaited_once()
    execution.submit.assert_not_awaited()
    assert gateway.outbox.next(gateway.channel.id, gateway.owner) is None
    receipt = Management(gateway.store, ChannelRegistry().generation).list(gateway.channel.id)[0]
    assert receipt.operation.message_id == "bad" and receipt.status == "pending"
    turn, _ = gateway.inbox.turn(good, gateway.owner)
    assert [item.inbox_id for item in gateway.inbox.claim(turn).batch] == [good]


@pytest.mark.asyncio
async def test_filter_failure_is_bounded_and_never_recalls_or_submits(gateway, tmp_path):
    event = gateway.receive("pending", approve=False)
    filters = Mock(evaluate=AsyncMock(side_effect=TimeoutError))
    execution = Mock(authorize=AsyncMock(), submit=AsyncMock())
    dispatcher = Dispatcher(gateway.inbox, gateway.outbox, execution, tmp_path, filters)
    for _ in range(gateway.channel.policy.filter_max_attempts):
        await dispatcher.process(event, gateway.owner)
    with gateway.sessions() as session:
        row = session.get(GatewayInbox, event)
        assert row.status == "blocked"
        assert row.filter_attempts == gateway.channel.policy.filter_max_attempts
        assert session.scalar(select(func.count()).select_from(GatewayDelivery)) == 0
    execution.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_recall_does_not_block_rejection_and_is_not_duplicated(gateway, tmp_path):
    gateway.store.save(
        gateway.channel.model_copy(
            update={
                "policy": gateway.channel.policy.model_copy(update={"rejection_agent_id": uuid4()})
            }
        ),
        gateway.user_id,
    )
    event = gateway.receive("rejected", chat="group", group=True, approve=False)
    current, _ = gateway.inbox.turn(event, gateway.owner)
    decision = recall_decision(current, "Generate a brief rejection.")
    filters = Mock(evaluate=AsyncMock(return_value=decision))
    execution = Mock(
        authorize=AsyncMock(),
        submit=AsyncMock(),
        submit_rejection=AsyncMock(return_value=uuid4()),
        result=AsyncMock(return_value=("completed", "Please choose a safe topic.")),
    )
    dispatcher = Dispatcher(gateway.inbox, gateway.outbox, execution, tmp_path, filters)
    await dispatcher.process(event, gateway.owner)
    await dispatcher.process(event, gateway.owner)
    execution.submit.assert_not_awaited()
    operation = Management(gateway.store, ChannelRegistry().generation).list(gateway.channel.id)[0]
    with gateway.sessions.begin() as session:
        session.get(GatewayManagementOperation, operation.id).status = "failed"
    rejection = gateway.outbox.next(gateway.channel.id, gateway.owner)
    assert rejection.event == "message"
    assert rejection.content == [ContentPart(text="Please choose a safe topic.")]
    with gateway.sessions() as session:
        assert session.scalar(select(func.count()).select_from(GatewayDelivery)) == 1
        assert session.scalar(select(func.count()).select_from(GatewayManagementOperation)) == 1


def test_channel_target_accepts_unbound_members_and_limits_enabled_conversations(gateway):
    identifier = gateway.receive("new-member", sender="unbound", chat="shared", group=True)
    current, _ = gateway.inbox.turn(identifier, gateway.owner)
    with gateway.sessions() as session:
        assert session.get(GatewayAccountBinding, current.identity_id) is None
        assert channel_target(
            session, gateway.store, current.channel_id, current.identity_id, current.message
        ) == (gateway.channel.target, gateway.channel.binding_revision)
        unknown = current.message.model_copy(
            update={
                "conversation": current.message.conversation.model_copy(
                    update={"external_id": "unknown"}
                )
            }
        )
        with pytest.raises(ForbiddenError, match="conversation"):
            channel_target(session, gateway.store, current.channel_id, current.identity_id, unknown)
    assert current.user_id == gateway.user_id
    assert current.message.actor.external_role == "member"
    assert current.target == gateway.channel.target


def test_rebinding_rejects_queued_work_and_invalidates_old_push_receipts(gateway):
    identifier = gateway.receive("first")
    current, _ = gateway.inbox.turn(identifier, gateway.owner)
    definition = gateway.store.get(gateway.channel.id)
    changed = definition.model_copy(
        update={"target": definition.target.model_copy(update={"target_id": uuid4()})}
    )
    with pytest.raises(ConflictError, match="Drain"):
        gateway.store.save(changed, gateway.user_id)
    gateway.inbox.ignore(current)
    assert len(push.known_subjects(gateway.store, gateway.channel.id)) == 1
    changed = gateway.store.save(changed, gateway.user_id)
    assert changed.binding_revision == definition.binding_revision + 1
    assert push.known_subjects(gateway.store, gateway.channel.id) == []
    restored = gateway.store.save(
        changed.model_copy(update={"target": definition.target}), gateway.user_id
    )
    assert restored.binding_revision == changed.binding_revision + 1
    with pytest.raises(ConflictError, match="target changed"):
        gateway.inbox.turn(identifier, gateway.owner)
    assert push.known_subjects(gateway.store, gateway.channel.id) == []


def test_inflight_ingress_fences_rebinding_and_snapshots_target(gateway):
    inserting, resume = Event(), Event()
    engine = gateway.sessions.kw["bind"]
    changed = gateway.channel.model_copy(
        update={"target": gateway.channel.target.model_copy(update={"target_id": uuid4()})}
    )

    def pause_receipt(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO gateway_inbox"):
            inserting.set()
            assert resume.wait(10), "Ingress transaction was not released"

    event.listen(engine, "before_cursor_execute", pause_receipt)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(gateway.receive, "inflight", approve=False)
            try:
                assert inserting.wait(10), "Ingress did not reach receipt insertion"
                with (
                    pytest.raises(OperationalError) as failure,
                    gateway.sessions.begin() as session,
                ):
                    session.execute(sql_text("SET LOCAL lock_timeout = '200ms'"))
                    gateway.store.save_in(session, changed, gateway.user_id)
                assert failure.value.orig.sqlstate == "55P03"
            finally:
                resume.set()
            identifier = pending.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", pause_receipt)
    with gateway.sessions() as session:
        row = session.get(GatewayInbox, identifier)
        assert row.status == "pending"
        assert (row.user_id, row.target_kind, row.target_id, row.binding_revision) == (
            gateway.user_id,
            gateway.channel.target.target_kind,
            gateway.channel.target.target_id,
            gateway.channel.binding_revision,
        )
    with pytest.raises(ConflictError, match="Drain"):
        gateway.store.save(changed, gateway.user_id)


@pytest.mark.parametrize("change_target", [False, True])
def test_old_connection_cannot_submit_after_channel_change(gateway, change_target):
    updates = (
        {"target": gateway.channel.target.model_copy(update={"target_id": uuid4()})}
        if change_target
        else {"name": "Updated channel"}
    )
    changed = gateway.store.save(gateway.channel.model_copy(update=updates), gateway.user_id)
    with pytest.raises(ConflictError, match="connection configuration changed"):
        gateway.receive("stale", approve=False, channel_revision=gateway.channel.revision)
    with gateway.sessions() as session:
        assert session.scalar(select(func.count()).select_from(GatewayInbox)) == 0
    identifier = gateway.receive("fresh", approve=False, channel_revision=changed.revision)
    current, _ = gateway.inbox.turn(identifier, gateway.owner)
    assert current.target == changed.target
    assert current.binding_revision == changed.binding_revision


def test_retry_preserves_receipt_target_and_rejects_rebinding(gateway):
    identifier = gateway.receive("retry", approve=False)
    gateway.inbox.fail(identifier, gateway.owner, "filter_unavailable")
    retry_inbox(gateway.store, gateway.channel.id, identifier, gateway.user_id)
    with gateway.sessions() as session:
        row = session.get(GatewayInbox, identifier)
        assert row.status == "pending"
        assert row.binding_revision == gateway.channel.binding_revision
        assert row.target_id == gateway.channel.target.target_id
        assert row.user_id == gateway.user_id
    gateway.inbox.fail(identifier, gateway.owner, "filter_unavailable")
    gateway.store.save(
        gateway.channel.model_copy(
            update={"target": gateway.channel.target.model_copy(update={"target_id": uuid4()})}
        ),
        gateway.user_id,
    )
    with pytest.raises(ConflictError, match="target changed"):
        retry_inbox(gateway.store, gateway.channel.id, identifier, gateway.user_id)
    with gateway.sessions() as session:
        assert session.get(GatewayInbox, identifier).status == "blocked"


def test_private_sessions_isolate_users_while_group_members_share_one_scope(gateway):
    turns = [
        gateway.inbox.turn(
            gateway.receive(event, sender=sender, chat=chat, group=group), gateway.owner
        )[0]
        for event, sender, chat, group in (
            ("private-alice", "alice", "private", False),
            ("private-bob", "bob", "private", False),
            ("group-alice", "alice", "shared", True),
            ("group-bob", "bob", "shared", True),
        )
    ]
    assert turns[0].source.session_scope != turns[1].source.session_scope
    assert turns[2].source.session_scope == turns[3].source.session_scope
    assert all(turn.target == gateway.channel.target for turn in turns)


def test_channel_revision_change_cannot_commit_an_old_moderation_recall(gateway):
    identifier = gateway.receive("review", chat="shared", group=True, approve=False)
    current, _ = gateway.inbox.turn(identifier, gateway.owner)
    definition = gateway.store.get(gateway.channel.id)
    gateway.store.save(definition, gateway.user_id)
    with pytest.raises(ConflictError, match="configuration changed"):
        gateway.outbox.reject(current, MessageFilterResult(action="drop"))
    with gateway.sessions() as session:
        assert session.get(GatewayInbox, identifier).status == "pending"
        assert session.scalar(select(func.count()).select_from(GatewayDelivery)) == 0


def test_recovered_batch_rechecks_admission_after_channel_policy_changes(gateway):
    first = gateway.receive("first", chat="group", group=True)
    second = gateway.receive("second", chat="group", group=True, sender="bob")
    current, _ = gateway.inbox.turn(first, gateway.owner)
    assert len(gateway.inbox.claim(current).batch) == 2
    definition = gateway.store.get(gateway.channel.id)
    gateway.store.save(definition, gateway.user_id)
    restored, _ = gateway.inbox.turn(first, gateway.owner)
    assert gateway.inbox.claim(restored) is None
    with gateway.sessions() as session:
        for identifier in (first, second):
            row = session.get(GatewayInbox, identifier)
            assert row.status == "pending"
            assert row.encrypted_context is None and row.queue_key is None


def test_rejection_recovery_keeps_original_blocked_and_does_not_pin_chat_fifo(gateway):
    gateway.store.save(
        gateway.channel.model_copy(
            update={
                "policy": gateway.channel.policy.model_copy(update={"rejection_agent_id": uuid4()})
            }
        ),
        gateway.user_id,
    )
    bad = gateway.receive("bad", chat="group", group=True, approve=False)
    turn, _ = gateway.inbox.turn(bad, gateway.owner)
    gateway.outbox.reject(
        turn, MessageFilterResult(action="drop", rejection_input="Refuse safely.")
    )
    assert bad in gateway.inbox.pending(gateway.channel.id)
    good = gateway.receive("good", chat="group", group=True)
    current, _ = gateway.inbox.turn(good, gateway.owner)
    assert [item.inbox_id for item in gateway.inbox.claim(current).batch] == [good]
    gateway.inbox.fail(bad, gateway.owner, "rejection_generation_failed")
    definition = gateway.store.get(gateway.channel.id)
    gateway.store.save(definition.model_copy(update={"name": "Renamed"}), gateway.user_id)
    retry_inbox(gateway.store, gateway.channel.id, bad, gateway.user_id)
    restored, _ = gateway.inbox.turn(bad, gateway.owner)
    assert restored.status == "responding" and restored.admission.action == "drop"
    with gateway.sessions.begin() as session:
        row = session.get(GatewayInbox, bad)
        row.status, row.run_id = "blocked", uuid4()
    with pytest.raises(ConflictError, match="execution recovery"):
        retry_inbox(gateway.store, gateway.channel.id, bad, gateway.user_id)
    with gateway.sessions() as session:
        assert session.get(GatewayInbox, bad).status == "blocked"


@pytest.mark.asyncio
async def test_native_rejection_run_has_no_session_and_never_contains_original(gateway, tmp_path):
    from opsmesh.agents.config.agent import AgentConfig
    from opsmesh.agents.manager import AgentDefinition
    from opsmesh.bootstrap.gateway import GatewayExecution
    from opsmesh.execution.instances.schemas import InstanceCreate
    from opsmesh.execution.instances.service import create_instance
    from opsmesh.execution.runs.models import ExecutionRun
    from opsmesh.users.service import current_user

    cipher = gateway.store.cipher
    definition = AgentDefinition(
        id="refusal", config=AgentConfig(name="Refusal", instructions="Refuse kindly.")
    )
    with gateway.sessions.begin() as session:
        actor = current_user(session, gateway.user_id)
        instance = create_instance(
            session,
            actor=actor,
            cipher=cipher,
            request=InstanceCreate(agent_id=definition.id, request_key="refusal"),
            load=lambda _: definition,
        )
        instance_id = instance.id
    gateway.store.save(
        gateway.channel.model_copy(
            update={
                "policy": gateway.channel.policy.model_copy(
                    update={"rejection_agent_id": instance_id}
                )
            }
        ),
        gateway.user_id,
    )
    current_definition = gateway.store.get(gateway.channel.id)
    gateway.store.save(
        current_definition.model_copy(
            update={
                "target": current_definition.target.model_copy(update={"target_id": instance_id})
            }
        ),
        gateway.user_id,
    )
    bad = gateway.receive(
        "bad", "Original private prohibited input", chat="group", group=True, approve=False
    )
    turn, _ = gateway.inbox.turn(bad, gateway.owner)
    decision = MessageFilterResult(action="drop", rejection_input='{"categories":["advertising"]}')
    gateway.outbox.reject(turn, decision)
    turn, _ = gateway.inbox.turn(bad, gateway.owner)
    execution = GatewayExecution(gateway.sessions, cipher, tmp_path / "history", tmp_path / "media")
    run_id = await execution.submit_rejection(turn)
    assert await execution.submit_rejection(turn) == run_id
    with gateway.sessions() as session:
        row = session.get(ExecutionRun, run_id)
        assert row.instance_id == instance_id and row.session_id is None
        payload = cipher.decrypt_payload(row.encrypted_input)
        assert payload["input"] == decision.rejection_input
        assert "Original private prohibited input" not in str(payload)
        assert session.scalar(select(func.count()).select_from(ExecutionRun)) == 1
        assert session.scalar(select(func.count()).select_from(GatewayDelivery)) == 0


def recall_decision(turn, notice=""):
    return FilterDecision(
        MessageFilterResult(
            action="drop",
            rejection_input=notice,
            operations=[
                RecallMessage(
                    conversation=ConversationRef(
                        scope=turn.message.actor.scope,
                        kind="group",
                        external_id=turn.message.conversation.external_id,
                    ),
                    message_id=turn.message.event_id,
                )
            ],
        ),
        OperationOrigin(
            kind="filter",
            actor_id=turn.user_id,
            resource_id="review/message_filter/check",
            generation="a" * 64,
            source_id=turn.inbox_id,
        ),
    )


def test_management_idempotency_and_uncertain_recovery(gateway):
    from opsmesh_sdk.channels import ChannelCapabilities

    from opsmesh.gateway.adapters.qq.management import QQManagement

    identifier = gateway.receive("moderated", chat="group", group=True, approve=False)
    turn, _ = gateway.inbox.turn(identifier, gateway.owner)
    decision = recall_decision(turn)
    service = Management(gateway.store, ChannelRegistry().generation)
    request_id = uuid4()
    first = service.submit(
        turn.channel_id, request_id, decision.origin, decision.result.operations[0]
    )
    assert (
        service.submit(turn.channel_id, request_id, decision.origin, decision.result.operations[0])
        == first
    )
    with pytest.raises(ConflictError, match="different input"):
        service.submit(
            turn.channel_id,
            request_id,
            decision.origin,
            decision.result.operations[0].model_copy(update={"reason": "different"}),
        )
    worker = ManagementWorker(
        gateway.store, AsyncMock(), lambda _: "a" * 64, ChannelRegistry().generation
    )
    adapter = Mock(
        capabilities=ChannelCapabilities(management=frozenset({"recall"})),
        management=Mock(capabilities=QQManagement.capabilities),
    )
    assert worker.claim(turn.channel_id, gateway.owner, adapter).id == first.id
    worker.recover(turn.channel_id, gateway.owner)
    assert service.get(turn.channel_id, first.id).status == "uncertain"
    assert worker.claim(turn.channel_id, gateway.owner, adapter) is None
    with pytest.raises(ConflictError, match="duplicate risk"):
        service.resolve(turn.channel_id, first.id, gateway.user_id, "retry")
    service.resolve(turn.channel_id, first.id, gateway.user_id, "cancel")
    assert service.get(turn.channel_id, first.id).error_code == "worker_interrupted"


def test_management_rechecks_generation_and_platform_message_age(gateway):
    from opsmesh_sdk.channels import ChannelCapabilities

    from opsmesh.gateway.adapters.qq.management import QQManagement

    identifier = gateway.receive("old", chat="group", group=True, approve=False)
    turn, _ = gateway.inbox.turn(identifier, gateway.owner)
    decision = recall_decision(turn)
    service = Management(gateway.store, ChannelRegistry().generation)
    receipt = service.submit(
        turn.channel_id, uuid4(), decision.origin, decision.result.operations[0]
    )
    adapter = Mock(
        capabilities=ChannelCapabilities(management=frozenset({"recall"})),
        management=Mock(capabilities=QQManagement.capabilities),
    )
    worker = ManagementWorker(
        gateway.store, AsyncMock(), lambda _: "b" * 64, ChannelRegistry().generation
    )
    assert worker.claim(turn.channel_id, gateway.owner, adapter) is None
    assert service.get(turn.channel_id, receipt.id).error_code == "plugin_changed"
    with gateway.sessions.begin() as session:
        row = session.get(GatewayInbox, identifier)
        old = turn.message.model_copy(
            update={"occurred_at": datetime.now(UTC) - timedelta(minutes=3)}
        )
        row.encrypted_payload = gateway.store.cipher.encrypt_payload(
            old.model_dump(mode="json")
        ).ciphertext
    receipt = service.submit(
        turn.channel_id, uuid4(), decision.origin, decision.result.operations[0]
    )
    worker = ManagementWorker(
        gateway.store, AsyncMock(), lambda _: "a" * 64, ChannelRegistry().generation
    )
    assert worker.claim(turn.channel_id, gateway.owner, adapter) is None
    assert service.get(turn.channel_id, receipt.id).status == "expired"


def test_destructive_filter_operation_requires_approval(gateway):
    from opsmesh_sdk.channel_management import MemberAction

    identifier = gateway.receive("remove", chat="group", group=True, approve=False)
    turn, _ = gateway.inbox.turn(identifier, gateway.owner)
    decision = recall_decision(turn)
    service = Management(gateway.store, ChannelRegistry().generation)
    receipt = service.submit(
        turn.channel_id,
        uuid4(),
        decision.origin,
        MemberAction(
            action="remove_member",
            conversation=decision.result.operations[0].conversation,
            member_id=turn.message.actor.external_id,
        ),
    )
    assert receipt.status == "awaiting_approval"
    assert (
        service.resolve(turn.channel_id, receipt.id, gateway.user_id, "approve").status == "pending"
    )
    with gateway.sessions() as session:
        assert session.get(GatewayManagementOperation, receipt.id).approved_by == gateway.user_id


def test_denied_management_effect_keeps_the_rejection_response_independent(gateway):
    definition = gateway.store.get(gateway.channel.id)
    group = definition.policy.groups["group"].model_copy(update={"management": ManagementPolicy()})
    gateway.store.save(
        definition.model_copy(
            update={
                "policy": definition.policy.model_copy(
                    update={"groups": {"group": group}, "rejection_agent_id": uuid4()}
                )
            }
        ),
        gateway.user_id,
    )
    identifier = gateway.receive("blocked-effect", chat="group", group=True, approve=False)
    turn, _ = gateway.inbox.turn(identifier, gateway.owner)
    decision = recall_decision(turn, "Refuse safely.")
    gateway.outbox.reject(turn, decision.result, decision.origin)
    restored, _ = gateway.inbox.turn(identifier, gateway.owner)
    assert restored.status == "responding" and restored.admission.action == "drop"
    receipt = Management(gateway.store, ChannelRegistry().generation).list(turn.channel_id)[0]
    assert receipt.status == "failed" and receipt.error_code == "authorization_denied"


def test_management_receipts_can_be_read_from_a_later_authorized_group_message(gateway):
    from opsmesh_sdk.channel_management import ConversationQuery

    resource = "management/tool/manage"
    definition = gateway.store.get(gateway.channel.id)
    groups = dict(definition.policy.groups)
    groups["group"] = groups["group"].model_copy(
        update={
            "management": ManagementPolicy(
                actions=frozenset({"permissions"}),
                grants={resource: ManagementGrant(actions=frozenset({"permissions"}))},
            )
        }
    )
    gateway.store.save(
        definition.model_copy(
            update={"policy": definition.policy.model_copy(update={"groups": groups})}
        ),
        gateway.user_id,
    )
    first = gateway.receive("first-admin", chat="group", group=True, role="admin")
    origin = OperationOrigin(
        kind="tool",
        actor_id=gateway.user_id,
        resource_id=resource,
        generation="a" * 64,
        source_id=first,
    )
    service = Management(gateway.store, ChannelRegistry().generation)
    receipt = service.submit(
        definition.id,
        uuid4(),
        origin,
        ConversationQuery(
            action="permissions",
            conversation=ConversationRef(scope="app", kind="group", external_id="group"),
        ),
    )
    followup = gateway.receive("followup-admin", chat="group", group=True, role="admin")
    assert (
        service.get(
            definition.id, receipt.id, origin=origin.model_copy(update={"source_id": followup})
        ).id
        == receipt.id
    )
    outsider = gateway.receive("member", chat="group", group=True, role="member")
    with pytest.raises(ForbiddenError):
        service.get(
            definition.id, receipt.id, origin=origin.model_copy(update={"source_id": outsider})
        )
