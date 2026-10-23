"""Acknowledge transport input only after encrypted durable receipt."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from opsmesh_sdk.channels import InboundMessage
from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from opsmesh.gateway.channels.leases import require_lease
from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.identity.conversations import Conversations, conversation_key
from opsmesh.gateway.identity.identities import Identities
from opsmesh.gateway.messaging.commands import command
from opsmesh.gateway.routing.service import resolve_route
from opsmesh.gateway.storage.models import GatewayAccountBinding, GatewayChannel, GatewayInbox
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, DomainError
from opsmesh.shared.serialization import canonical_json, payload_digest


class Inbox:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def receive(self, channel_id: UUID, owner: str, message: InboundMessage) -> None:
        payload = message.model_dump(mode="json")
        if len(canonical_json(payload).encode()) > 1_000_000:
            raise ValueError("Gateway event exceeds the size limit")
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": "gateway-ingress:"
                    + payload_digest(
                        {
                            "channel": str(channel_id),
                            "scope": message.actor.scope,
                            "actor": message.actor.external_id,
                        }
                    )
                },
            )
            channel = session.get(GatewayChannel, channel_id)
            if channel is None or not channel.enabled:
                raise ConflictError("Channel is disabled")
            identity = Identities(self.store).observe(session, channel_id, message.actor)
            Conversations(self.store).observe(session, channel_id, identity.id, message)
            encrypted = self.store.cipher.encrypt_payload(payload)
            session.execute(
                insert(GatewayInbox)
                .values(
                    id=uuid4(),
                    channel_id=channel_id,
                    identity_id=identity.id,
                    event_key=payload_digest(
                        {"scope": message.actor.scope, "event": message.event_id}
                    ),
                    conversation_key=conversation_key(message),
                    encrypted_payload=encrypted.ciphertext,
                    key_id=encrypted.key_id,
                    status="pending",
                    control=command(message),
                )
                .on_conflict_do_nothing(
                    index_elements=[GatewayInbox.channel_id, GatewayInbox.event_key]
                )
            )

    def pending(self, channel_id: UUID) -> list[UUID]:
        with self.store.sessions() as session:
            channel = session.get(GatewayChannel, channel_id)
            if channel is None or not channel.enabled:
                return []
            debounce = self.store.definition(channel).policy.debounce_seconds
            cutoff = datetime.now(UTC) - timedelta(seconds=debounce)
            earlier, later = aliased(GatewayInbox), aliased(GatewayInbox)
            result: list[UUID] = []
            for status in ("pending", "submitted"):
                result.extend(
                    session.scalars(
                        select(GatewayInbox.id)
                        .where(
                            GatewayInbox.channel_id == channel_id,
                            GatewayInbox.status == status,
                            or_(
                                GatewayInbox.control.is_not(None),
                                ~select(earlier.id)
                                .where(
                                    earlier.channel_id == GatewayInbox.channel_id,
                                    earlier.identity_id == GatewayInbox.identity_id,
                                    earlier.conversation_key == GatewayInbox.conversation_key,
                                    earlier.status.in_(("pending", "submitted")),
                                    or_(
                                        earlier.created_at < GatewayInbox.created_at,
                                        and_(
                                            earlier.created_at == GatewayInbox.created_at,
                                            earlier.id < GatewayInbox.id,
                                        ),
                                    ),
                                )
                                .exists(),
                            ),
                            *(
                                [
                                    or_(
                                        GatewayInbox.control.is_not(None),
                                        GatewayInbox.created_at
                                        <= datetime.now(UTC) - timedelta(seconds=30),
                                        ~select(later.id)
                                        .where(
                                            later.channel_id == GatewayInbox.channel_id,
                                            later.identity_id == GatewayInbox.identity_id,
                                            later.conversation_key == GatewayInbox.conversation_key,
                                            later.status == "pending",
                                            later.created_at > cutoff,
                                        )
                                        .exists(),
                                    )
                                ]
                                if status == "pending" and debounce
                                else []
                            ),
                        )
                        .order_by(GatewayInbox.created_at, GatewayInbox.id)
                        .limit(50)
                    )
                )
            return result

    def turn(self, inbox_id: UUID, owner: str) -> tuple[GatewayTurn, UUID | None]:
        with self.store.sessions.begin() as session:
            channel_id = session.scalar(
                select(GatewayInbox.channel_id).where(GatewayInbox.id == inbox_id)
            )
            if channel_id is None:
                raise ConflictError("Inbox event no longer exists")
            require_lease(session, channel_id, owner)
            row = session.get(GatewayInbox, inbox_id, with_for_update=True)
            if row is None:
                raise ConflictError("Inbox event no longer exists")
            message = InboundMessage.model_validate(
                self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )
            user_id, route = resolve_route(session, row.channel_id, row.identity_id, message)
            channel = session.get(GatewayChannel, row.channel_id)
            if channel is None or not channel.enabled or channel.deleted:
                raise ConflictError("Channel is disabled")
            policy = self.store.definition(channel).policy
            if row.route_id and (
                row.user_id != user_id
                or row.route_id != route.id
                or row.route_revision != route.revision
            ):
                raise ConflictError("Account binding or route changed after dispatch")
            context: dict[str, object] = {"messages": [], "ids": []}
            if row.encrypted_context is not None:
                context = self.store.cipher.decrypt_payload(row.encrypted_context)
                if "message" in context:
                    message = InboundMessage.model_validate(context["message"])
            elif row.run_id is None and policy.activates(message):
                group_policy = policy.group_for(message)
                if (
                    message.conversation.kind != "private"
                    and group_policy.visibility != "mention_only"
                    and group_policy.history_limit
                    and group_policy.history_ttl_seconds
                ):
                    cutoff = datetime.now(UTC) - timedelta(seconds=group_policy.history_ttl_seconds)
                    candidates = session.scalars(
                        select(GatewayInbox)
                        .join(
                            GatewayAccountBinding,
                            GatewayAccountBinding.identity_id == GatewayInbox.identity_id,
                        )
                        .where(
                            GatewayInbox.channel_id == row.channel_id,
                            GatewayInbox.conversation_key == row.conversation_key,
                            GatewayInbox.status == "passive",
                            GatewayInbox.created_at >= cutoff,
                            GatewayInbox.created_at <= row.created_at,
                            GatewayInbox.user_id == GatewayAccountBinding.user_id,
                            *(
                                [GatewayInbox.user_id == user_id]
                                if group_policy.history_scope == "user"
                                else []
                            ),
                        )
                        .order_by(GatewayInbox.created_at.desc(), GatewayInbox.id.desc())
                        .limit(group_policy.history_limit)
                    ).all()
                    messages, identifiers = [], []
                    for item in reversed(candidates):
                        previous = InboundMessage.model_validate(
                            self.store.cipher.decrypt_payload(
                                item.encrypted_payload, key_id=item.key_id
                            )
                        )
                        try:
                            previous_user, previous_route = resolve_route(
                                session, row.channel_id, item.identity_id, previous
                            )
                        except DomainError:
                            continue
                        if (
                            previous_user != item.user_id
                            or previous_route.id != item.route_id
                            or previous_route.revision != item.route_revision
                        ):
                            continue
                        messages.append(previous.model_dump(mode="json"))
                        identifiers.append(str(item.id))
                    context = {"messages": messages, "ids": identifiers}
                if policy.debounce_seconds and command(message) is None:
                    content = list(message.content)
                    batched = []
                    for item in session.scalars(
                        select(GatewayInbox)
                        .where(
                            GatewayInbox.channel_id == row.channel_id,
                            GatewayInbox.identity_id == row.identity_id,
                            GatewayInbox.conversation_key == row.conversation_key,
                            GatewayInbox.status == "pending",
                            GatewayInbox.id != row.id,
                            GatewayInbox.created_at >= row.created_at,
                        )
                        .order_by(GatewayInbox.created_at, GatewayInbox.id)
                        .limit(20)
                        .with_for_update()
                    ):
                        following = InboundMessage.model_validate(
                            self.store.cipher.decrypt_payload(
                                item.encrypted_payload, key_id=item.key_id
                            )
                        )
                        if (
                            command(following) is not None
                            or following.actor != message.actor
                            or not policy.activates(following)
                            or len(content) + len(following.content) > 100
                        ):
                            break
                        content.extend(following.content)
                        merged = message.model_copy(update={"content": content})
                        if len(canonical_json(merged.model_dump(mode="json")).encode()) > 1_000_000:
                            content = content[: -len(following.content)]
                            break
                        batched.append(item)
                    if batched:
                        message = message.model_copy(update={"content": content})
                        context["message"] = message.model_dump(mode="json")
                        context["batched_ids"] = [str(item.id) for item in batched]
                        for item in batched:
                            item.status = "batched"
                            item.user_id, item.route_id, item.route_revision = (
                                user_id,
                                route.id,
                                route.revision,
                            )
                row.encrypted_context = self.store.cipher.encrypt_payload(context).ciphertext
            if row.run_id is None:
                group = policy.group_for(message)
                for identifier in TypeAdapter(list[UUID]).validate_python(context["ids"]):
                    previous_row = session.get(GatewayInbox, identifier)
                    if previous_row is None:
                        raise ConflictError("A captured context receipt no longer exists")
                    previous_message = InboundMessage.model_validate(
                        self.store.cipher.decrypt_payload(
                            previous_row.encrypted_payload, key_id=previous_row.key_id
                        )
                    )
                    previous_user, previous_route = resolve_route(
                        session, row.channel_id, previous_row.identity_id, previous_message
                    )
                    if (
                        previous_user != previous_row.user_id
                        or previous_route.id != previous_row.route_id
                        or previous_route.revision != previous_row.route_revision
                        or previous_row.status != "passive"
                        or (group.history_scope == "user" and previous_user != user_id)
                    ):
                        raise ConflictError("A captured context binding or route changed")
            message = policy.prepare_context(message, datetime.now(UTC).timestamp())
            return GatewayTurn(
                inbox_id=row.id,
                channel_id=row.channel_id,
                identity_id=row.identity_id,
                user_id=user_id,
                route=route,
                message=message,
                lease_owner=owner,
                policy=policy,
                recent_messages=TypeAdapter(list[InboundMessage]).validate_python(
                    context["messages"]
                ),
                context_ids=TypeAdapter(list[UUID]).validate_python(context["ids"]),
                event_cursor=row.event_cursor,
            ), row.run_id

    def passive(self, turn: GatewayTurn) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.run_id is not None:
                raise ConflictError("A submitted event cannot become passive context")
            row.status = "passive" if turn.message.conversation.kind != "private" else "completed"
            row.route_id, row.route_revision, row.user_id = (
                turn.route.id,
                turn.route.revision,
                turn.user_id,
            )

    def submitted(self, turn: GatewayTurn, run_id: UUID) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            row.status, row.run_id, row.route_id, row.user_id = (
                "submitted",
                run_id,
                turn.route.id,
                turn.user_id,
            )
            row.route_revision = turn.route.revision

    def fail(self, inbox_id: UUID, owner: str, error: str) -> None:
        with self.store.sessions.begin() as session:
            channel_id = session.scalar(
                select(GatewayInbox.channel_id).where(GatewayInbox.id == inbox_id)
            )
            if channel_id is None:
                return
            require_lease(session, channel_id, owner)
            row = session.get(GatewayInbox, inbox_id, with_for_update=True)
            if row and row.status != "completed":
                row.status, row.error_code = "blocked", error
