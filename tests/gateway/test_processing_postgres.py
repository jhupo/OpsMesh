"""Durable gateway behavior against an explicitly configured disposable database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from opsmesh_sdk.channels import ContentPart, InboundMessage
from opsmesh_sdk.messages import MessageFilterResult
from sqlalchemy import func, select

from opsmesh.gateway.channels.leases import ChannelLeases
from opsmesh.gateway.channels.policy import GatewayPolicy, GroupPolicy
from opsmesh.gateway.contracts import (
    ChannelDefinition,
    DeliveryResolution,
    GatewayEvent,
    GatewayEvents,
    PushCreate,
    RouteDefinition,
)
from opsmesh.gateway.identity.identities import Identities
from opsmesh.gateway.messaging import push
from opsmesh.gateway.messaging.dispatcher import Dispatcher
from opsmesh.gateway.messaging.inbox import Inbox
from opsmesh.gateway.messaging.outbox import Outbox
from opsmesh.gateway.messaging.recovery import resolve_delivery
from opsmesh.gateway.routing.service import Routing, resolve_route
from opsmesh.gateway.storage.models import GatewayAccountBinding, GatewayDelivery, GatewayInbox
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
            policy=GatewayPolicy(
                group=GroupPolicy(session_scope="group", allow_recall=True),
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
    inbox, outbox = Inbox(store), Outbox(store)
    identities, routing = Identities(store), Routing(store)
    observed = {}
    counter = 0
    target_id = uuid4()

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
    ):
        nonlocal counter
        message = InboundMessage.model_validate(
            {
                "event_id": event,
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
        inbox.receive(definition.id, owner, message)
        with database_sessions.begin() as session:
            row = session.scalar(
                select(GatewayInbox).where(
                    GatewayInbox.channel_id == definition.id,
                    GatewayInbox.event_key == payload_digest({"scope": "app", "event": event}),
                )
            )
            counter += 1
            row.created_at = datetime.now(UTC) - timedelta(seconds=10 - counter / 100)
            identifier, identity_id = row.id, row.identity_id
        if identity_id not in observed:
            identities.bind(definition.id, identity_id, user_id, user_id)
            observed[identity_id] = set()
        if chat not in observed[identity_id]:
            routing.save(
                RouteDefinition(
                    id=uuid4(),
                    channel_id=definition.id,
                    identity_id=identity_id,
                    conversation_id=chat,
                    target_kind="agent",
                    target_id=target_id,
                    allow_group=group,
                ),
                user_id,
            )
            observed[identity_id].add(chat)
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


def test_debounce_waits_for_quiet_and_preserves_batch_after_recovery(gateway):
    first = gateway.receive("first", "Part one")
    second = gateway.receive("second", "Part two")
    control = gateway.receive("cancel", "/cancel")
    with gateway.sessions.begin() as session:
        session.get(GatewayInbox, first).created_at = datetime.now(UTC) - timedelta(seconds=0.05)
        session.get(GatewayInbox, second).created_at = datetime.now(UTC)
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    assert gateway.inbox.claim(turn) is None
    assert control in gateway.inbox.pending(gateway.channel.id)
    with gateway.sessions.begin() as session:
        session.get(GatewayInbox, first).created_at = datetime.now(UTC) - timedelta(seconds=1)
        session.get(GatewayInbox, second).created_at = datetime.now(UTC) - timedelta(seconds=0.9)
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


def test_group_batch_preserves_senders_and_rechecks_every_binding(gateway):
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
    with pytest.raises(ForbiddenError):
        gateway.inbox.turn(first, gateway.owner)


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
    filters = Mock(
        evaluate=AsyncMock(return_value=MessageFilterResult(action="recall", reason="spam"))
    )
    execution = Mock(authorize=AsyncMock(), submit=AsyncMock())
    dispatcher = Dispatcher(gateway.inbox, gateway.outbox, execution, tmp_path, filters)
    await dispatcher.process(bad, gateway.owner)
    filters.evaluate.assert_awaited_once()
    execution.submit.assert_not_awaited()
    recall = gateway.outbox.next(gateway.channel.id, gateway.owner)
    assert recall.event == "recall" and recall.address["msg_id"] == "bad"
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


def test_shared_group_route_accepts_unbound_members_without_promoting_group_roles(gateway):
    seed_id = gateway.receive("observed", chat="shared", group=True)
    seed, _ = gateway.inbox.turn(seed_id, gateway.owner)
    from opsmesh.gateway.identity.conversations import conversation_key

    route = Routing(gateway.store).save(
        RouteDefinition(
            id=uuid4(),
            channel_id=seed.channel_id,
            conversation_key=conversation_key(seed.message),
            user_id=gateway.user_id,
            target_kind="agent",
            target_id=seed.route.target_id,
            allow_group=True,
        ),
        gateway.user_id,
    )
    original = seed.message.model_copy(
        update={
            "event_id": "new-member",
            "actor": seed.message.actor.model_copy(
                update={"external_id": "unbound-member", "external_role": "member"}
            ),
        }
    )
    gateway.inbox.receive(gateway.channel.id, gateway.owner, original)
    with gateway.sessions() as session:
        row = session.scalar(
            select(GatewayInbox).where(
                GatewayInbox.event_key == payload_digest({"scope": "app", "event": "new-member"})
            )
        )
        identifier = row.id
        assert session.get(GatewayAccountBinding, row.identity_id) is None
    current, _ = gateway.inbox.turn(identifier, gateway.owner)
    assert current.user_id == gateway.user_id and current.route == route
    assert current.message.actor.external_role == "member"
    with gateway.sessions() as session:
        # A personal binding cannot replace the explicitly configured shared principal.
        assert resolve_route(session, seed.channel_id, seed.identity_id, seed.message) == (
            gateway.user_id,
            route,
        )
        private = original.model_copy(
            update={"conversation": original.conversation.model_copy(update={"kind": "private"})}
        )
        with pytest.raises(ForbiddenError):
            resolve_route(session, current.channel_id, current.identity_id, private)
    Routing(gateway.store).save(route.model_copy(update={"enabled": False}), gateway.user_id)
    with pytest.raises(ForbiddenError, match="disabled"):
        gateway.inbox.turn(identifier, gateway.owner)


def test_channel_revision_change_cannot_commit_an_old_moderation_recall(gateway):
    identifier = gateway.receive("review", chat="shared", group=True, approve=False)
    current, _ = gateway.inbox.turn(identifier, gateway.owner)
    definition = gateway.store.get(gateway.channel.id)
    gateway.store.save(definition, gateway.user_id)
    with pytest.raises(ConflictError, match="configuration changed"):
        gateway.outbox.complete(
            current, [], event="recall", admission=MessageFilterResult(action="recall")
        )
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
