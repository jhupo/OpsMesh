"""Proactive delivery uses verified conversation receipts and the normal outbox."""

from uuid import UUID

from opsmesh_sdk.channels import ContentPart, InboundMessage
from pydantic import TypeAdapter
from sqlalchemy import select, text

from opsmesh.gateway.contracts import GatewayTurn, PushCreate
from opsmesh.gateway.messaging.outbox import Outbox
from opsmesh.gateway.routing.service import resolve_route
from opsmesh.gateway.storage.models import GatewayChannel, GatewayInbox, GatewayPush
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, DomainError, InvalidArgumentError, NotFoundError
from opsmesh.shared.serialization import canonical_json, payload_digest


def known_subjects(store: GatewayStore, channel_id: UUID) -> list[GatewayTurn]:
    with store.sessions() as session:
        # PostgreSQL DISTINCT ON retains one newest settled receipt per actor/chat.
        rows = session.scalars(
            select(GatewayInbox)
            .where(
                GatewayInbox.channel_id == channel_id,
                GatewayInbox.status.in_(("completed", "passive", "context_consumed")),
            )
            .distinct(GatewayInbox.identity_id, GatewayInbox.conversation_key)
            .order_by(
                GatewayInbox.identity_id,
                GatewayInbox.conversation_key,
                GatewayInbox.created_at.desc(),
                GatewayInbox.id.desc(),
            )
        ).all()
        result, destinations = [], set()
        for row in sorted(rows, key=lambda item: (item.created_at, item.id), reverse=True):
            message = InboundMessage.model_validate(
                store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )
            try:
                user_id, route = resolve_route(session, channel_id, row.identity_id, message)
            except DomainError:
                continue
            if (row.user_id, row.route_id, row.route_revision) != (
                user_id,
                route.id,
                route.revision,
            ):
                continue
            destination = payload_digest(
                {
                    "scope": message.actor.scope,
                    "kind": message.reply_address.get("kind"),
                    "target": message.reply_address.get("target"),
                }
            )
            if destination in destinations:
                continue
            destinations.add(destination)
            result.append(
                GatewayTurn(
                    inbox_id=row.id,
                    lease_owner="",
                    channel_id=channel_id,
                    identity_id=row.identity_id,
                    user_id=user_id,
                    route=route,
                    message=message,
                )
            )
        return result


def recipients(store: GatewayStore, channel_id: UUID, body: PushCreate) -> list[GatewayTurn]:
    if len(body.recipients) != len(set(body.recipients)):
        raise InvalidArgumentError("Duplicate push recipients")
    if len(canonical_json(body.model_dump(mode="json")).encode()) > 1_000_000:
        raise InvalidArgumentError("Push content exceeds the size limit")
    if body.broadcast:
        turns = known_subjects(store, channel_id)
        if not turns:
            raise InvalidArgumentError("No currently bound subjects are available for broadcast")
        if len(turns) * len(body.content) > 10_000:
            raise InvalidArgumentError(
                "Broadcast exceeds 10000 deliveries; select recipient batches"
            )
        return turns
    if len(body.content) * len(body.recipients) > 500:
        raise InvalidArgumentError("A push may enqueue at most 500 deliveries")
    with store.sessions() as session:
        turns = []
        destinations = set()
        for identifier in body.recipients:
            row = session.get(GatewayInbox, identifier)
            if row is None or row.channel_id != channel_id:
                raise NotFoundError("Push recipient receipt does not exist")
            if row.status not in {"completed", "passive", "context_consumed"}:
                raise ConflictError("Push recipients require a settled conversation receipt")
            message = InboundMessage.model_validate(
                store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )
            user_id, route = resolve_route(session, channel_id, row.identity_id, message)
            if (row.user_id, row.route_id, row.route_revision) != (
                user_id,
                route.id,
                route.revision,
            ):
                raise ConflictError("Push recipient binding or route changed")
            destination = payload_digest(
                {
                    "kind": message.reply_address.get("kind"),
                    "target": message.reply_address.get("target"),
                }
            )
            if destination in destinations:
                raise InvalidArgumentError("Multiple receipts select the same push destination")
            destinations.add(destination)
            turns.append(
                GatewayTurn(
                    inbox_id=row.id,
                    lease_owner="",
                    channel_id=channel_id,
                    identity_id=row.identity_id,
                    user_id=user_id,
                    route=route,
                    message=message,
                )
            )
        return turns


def enqueue(
    store: GatewayStore,
    channel_id: UUID,
    actor_id: UUID,
    body: PushCreate,
    turns: list[GatewayTurn],
    content: list[ContentPart],
) -> list[UUID]:
    fingerprint = payload_digest(body.model_dump(mode="json"))
    with store.sessions.begin() as session:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "gateway-push:" + str(body.request_id)},
        )
        previous = session.get(GatewayPush, body.request_id)
        if previous is not None:
            if (
                previous.channel_id != channel_id
                or previous.actor_id != actor_id
                or previous.fingerprint != fingerprint
            ):
                raise ConflictError("Push request ID already has different content")
            return TypeAdapter(list[UUID]).validate_python(
                store.cipher.decrypt_payload(previous.encrypted_result, key_id=previous.key_id)[
                    "deliveries"
                ]
            )
        channel = session.get(GatewayChannel, channel_id, with_for_update=True)
        if channel is None or not channel.enabled or channel.deleted:
            raise ConflictError("Channel is disabled")
        identifiers = []
        # All receipt locks use a stable order, shared with ordinary reply enqueueing.
        for turn in sorted(turns, key=lambda item: item.inbox_id):
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            user_id, route = resolve_route(session, channel_id, turn.identity_id, turn.message)
            if (user_id, route) != (turn.user_id, turn.route):
                raise ConflictError("Push recipient changed during authorization")
            # Expiring reply credentials are not valid proactive destinations.
            address = {
                key: value
                for key, value in turn.message.reply_address.items()
                if key
                not in {"msg_id", "event_id", "webhook", "expires_at", "msg_seq", "stream_msg_id"}
            }
            message = turn.message.model_copy(update={"reply_address": address})
            identifiers.extend(
                Outbox(store).enqueue(
                    session, turn.model_copy(update={"message": message}), content
                )
            )
        encrypted = store.cipher.encrypt_payload(
            {"deliveries": [str(item) for item in identifiers]}
        )
        session.add(
            GatewayPush(
                id=body.request_id,
                channel_id=channel_id,
                actor_id=actor_id,
                fingerprint=fingerprint,
                encrypted_result=encrypted.ciphertext,
                key_id=encrypted.key_id,
            )
        )
        store.audit(session, "push.enqueue", actor_id, body.request_id)
        return identifiers
