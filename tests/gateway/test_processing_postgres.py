"""Durable gateway behavior against an explicitly configured disposable database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from opsmesh_sdk.channels import ContentPart, InboundMessage
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
from opsmesh.gateway.messaging.inbox import Inbox
from opsmesh.gateway.messaging.outbox import Outbox
from opsmesh.gateway.messaging.recovery import resolve_delivery
from opsmesh.gateway.routing.service import Routing
from opsmesh.gateway.storage.models import GatewayDelivery, GatewayInbox
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
                group=GroupPolicy(history_scope="group"),
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

    def receive(
        event, text="Hello", *, sender="alice", chat="private", group=False, mentioned=True
    ):
        nonlocal counter
        message = InboundMessage.model_validate(
            {
                "event_id": event,
                "actor": {
                    "scope": "app",
                    "id_kind": "member_openid" if group else "user_openid",
                    "external_id": sender,
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
                    target_id=uuid4(),
                    allow_group=group,
                ),
                user_id,
            )
            observed[identity_id].add(chat)
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
        session.get(GatewayInbox, second).created_at = datetime.now(UTC)
    assert first not in gateway.inbox.pending(gateway.channel.id)
    assert control in gateway.inbox.pending(gateway.channel.id)
    with gateway.sessions.begin() as session:
        session.get(GatewayInbox, second).created_at = session.get(
            GatewayInbox, first
        ).created_at + timedelta(microseconds=1)
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    assert [part.text for part in turn.message.content] == ["Part one", "Part two"]
    with gateway.sessions() as session:
        assert session.get(GatewayInbox, second).status == "batched"
        assert session.get(GatewayInbox, control).status == "pending"
    restored, _ = Inbox(gateway.store).turn(first, gateway.owner)
    assert restored.message == turn.message


def test_fifo_keeps_control_interruptible_while_a_run_is_active(gateway):
    first = gateway.receive("first")
    second = gateway.receive("second")
    control = gateway.receive("cancel", "/cancel")
    # Disable batching to verify the independent per-conversation FIFO contract.
    definition = gateway.store.get(gateway.channel.id)
    gateway.store.save(
        definition.model_copy(
            update={"policy": definition.policy.model_copy(update={"debounce_seconds": 0})}
        ),
        gateway.user_id,
    )
    turn, _ = gateway.inbox.turn(first, gateway.owner)
    gateway.inbox.submitted(turn, uuid4())
    pending = gateway.inbox.pending(gateway.channel.id)
    assert first in pending and control in pending and second not in pending
    gateway.outbox.complete(turn, [ContentPart(text="Done")])
    assert second in gateway.inbox.pending(gateway.channel.id)


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
        gateway.inbox.passive(turn)
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


def test_frozen_shared_history_is_rejected_after_sender_unbind(gateway):
    previous, _ = gateway.inbox.turn(
        gateway.receive("passive", sender="alice", chat="group", group=True, mentioned=False),
        gateway.owner,
    )
    gateway.inbox.passive(previous)
    current = gateway.receive("active", sender="bob", chat="group", group=True)
    turn, _ = gateway.inbox.turn(current, gateway.owner)
    assert turn.context_ids == [previous.inbox_id]
    gateway.identities.unbind(previous.identity_id, gateway.user_id)
    with pytest.raises(ForbiddenError):
        gateway.inbox.turn(current, gateway.owner)
