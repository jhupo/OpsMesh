"""Durable receipts, individual admission and ordered conversation batches."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from opsmesh_sdk.channels import InboundMessage
from opsmesh_sdk.messages import MessageFilterResult
from pydantic import TypeAdapter
from sqlalchemy import func, or_, select, text, true, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from opsmesh.gateway.channels.leases import require_lease
from opsmesh.gateway.contracts import BatchMessage, GatewayTurn
from opsmesh.gateway.identity.conversations import Conversations, conversation_key
from opsmesh.gateway.identity.identities import Identities
from opsmesh.gateway.messaging.commands import command
from opsmesh.gateway.routing.service import authorize_turn, resolve_route
from opsmesh.gateway.storage.models import GatewayChannel, GatewayInbox
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
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
                    "key": "gateway-identity:"
                    + payload_digest(
                        {
                            "channel": str(channel_id),
                            "scope": message.actor.scope,
                            "kind": message.actor.id_kind,
                            "actor": message.actor.external_id,
                        }
                    )
                },
            )
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"gateway-ingress:{channel_id}:{conversation_key(message)}"},
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
            result: list[UUID] = []
            earlier = aliased(GatewayInbox)
            for statuses in (("submitted", "dispatching"), ("pending",), ("ready",)):
                head_only = (
                    or_(
                        GatewayInbox.control.is_not(None),
                        ~select(earlier.id)
                        .where(
                            earlier.queue_key == GatewayInbox.queue_key,
                            earlier.status == "ready",
                            earlier.control.is_(None),
                            tuple_(earlier.created_at, earlier.id)
                            < tuple_(GatewayInbox.created_at, GatewayInbox.id),
                        )
                        .exists(),
                    )
                    if statuses == ("ready",)
                    else true()
                )
                result.extend(
                    session.scalars(
                        select(GatewayInbox.id)
                        .where(
                            GatewayInbox.channel_id == channel_id,
                            GatewayInbox.status.in_(statuses),
                            head_only,
                            or_(
                                GatewayInbox.filter_retry_at.is_(None),
                                GatewayInbox.filter_retry_at <= datetime.now(UTC),
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
            row = session.get(GatewayInbox, inbox_id)
            assert row is not None
            message = InboundMessage.model_validate(
                self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )
            user_id, route = resolve_route(session, row.channel_id, row.identity_id, message)
            channel = session.get(GatewayChannel, row.channel_id)
            if channel is None or not channel.enabled or channel.deleted:
                raise ConflictError("Channel is disabled")
            if row.route_id and (row.user_id, row.route_id, row.route_revision) != (
                user_id,
                route.id,
                route.revision,
            ):
                raise ConflictError("Account binding or route changed after admission")
            context = (
                self.store.cipher.decrypt_payload(row.encrypted_context)
                if row.encrypted_context
                else {}
            )
            turn = GatewayTurn(
                inbox_id=row.id,
                channel_id=row.channel_id,
                identity_id=row.identity_id,
                user_id=user_id,
                route=route,
                message=message,
                lease_owner=owner,
                policy=self.store.definition(channel).policy,
                channel_revision=channel.revision,
                status=row.status,
                queue_key=row.queue_key,
                batch=TypeAdapter(list[BatchMessage]).validate_python(context.get("batch", [])),
                event_cursor=row.event_cursor,
            )
            authorize_turn(session, turn)
            return turn, row.run_id

    def approved(self, turn: GatewayTurn, result: MessageFilterResult) -> None:
        with self.store.sessions.begin() as session:
            authorize_turn(session, turn)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            channel = session.get(GatewayChannel, turn.channel_id)
            assert row is not None and channel is not None
            if channel.revision != turn.channel_revision:
                raise ConflictError("Channel configuration changed during message admission")
            if row.status != "pending" or result.action != "allow":
                raise ConflictError("Only a pending allowed message can enter the queue")
            row.status, row.filter_revision, row.queue_key = (
                "ready",
                channel.revision,
                turn.source.session_scope,
            )
            row.user_id, row.route_id, row.route_revision = (
                turn.user_id,
                turn.route.id,
                turn.route.revision,
            )
            row.error_code, row.filter_retry_at = None, None
            row.encrypted_context = self.store.cipher.encrypt_payload(
                {
                    "admission": result.model_dump(mode="json"),
                    "filters": list(turn.policy.message_filters),
                }
            ).ciphertext

    def filter_failed(self, turn: GatewayTurn, error: str) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.status != "pending":
                return
            row.filter_attempts += 1
            row.error_code = error
            row.filter_retry_at = datetime.now(UTC) + timedelta(
                seconds=turn.policy.filter_retry_seconds
            )
            if row.filter_attempts >= turn.policy.filter_max_attempts:
                row.status = "blocked"

    def claim(self, turn: GatewayTurn) -> GatewayTurn | None:
        with self.store.sessions.begin() as session:
            authorize_turn(session, turn)
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "gateway-queue:" + str(turn.source.session_scope)},
            )
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.status not in {"ready", "dispatching"}:
                return None
            channel = session.get(GatewayChannel, turn.channel_id)
            assert channel is not None
            if row.filter_revision != channel.revision:
                for identifier in [item.inbox_id for item in turn.batch] or [row.id]:
                    child = session.get(GatewayInbox, identifier, with_for_update=True)
                    if child is not None and child.run_id is None:
                        child.status, child.encrypted_context = "pending", None
                        child.queue_key, child.filter_revision = None, None
                        child.filter_attempts, child.filter_retry_at = 0, None
                return None
            if row.status == "dispatching":
                return turn
            if command(turn.message) is not None:
                row.status = "dispatching"
                return turn
            active = (
                GatewayInbox.channel_id == turn.channel_id,
                GatewayInbox.conversation_key == row.conversation_key,
                GatewayInbox.control.is_(None),
                GatewayInbox.status.in_(("pending", "ready", "dispatching", "submitted")),
            )
            if session.scalar(
                select(GatewayInbox.id)
                .where(
                    *active,
                    tuple_(GatewayInbox.created_at, GatewayInbox.id) < (row.created_at, row.id),
                    or_(GatewayInbox.status == "pending", GatewayInbox.queue_key == row.queue_key),
                )
                .limit(1)
            ):
                return None
            latest = session.scalar(select(func.max(GatewayInbox.created_at)).where(*active))
            assert latest is not None
            candidates = session.scalars(
                select(GatewayInbox)
                .where(
                    *active,
                    tuple_(GatewayInbox.created_at, GatewayInbox.id) >= (row.created_at, row.id),
                    or_(GatewayInbox.status == "pending", GatewayInbox.queue_key == row.queue_key),
                )
                .order_by(GatewayInbox.created_at, GatewayInbox.id)
                .limit(turn.policy.batch_max_messages)
                .with_for_update()
            ).all()
            # Unadmitted earlier receipts remain an ordering barrier. Different authorized
            # principals/destinations have separate queues even in a shared external group.
            now = datetime.now(UTC)
            if turn.policy.debounce_seconds and (
                (now - latest).total_seconds() < turn.policy.debounce_seconds
                and (now - row.created_at).total_seconds() < turn.policy.batch_max_wait_seconds
            ):
                return None
            batch: list[BatchMessage] = []
            total = 0
            for item in candidates:
                if (item.created_at, item.id) < (row.created_at, row.id):
                    continue
                if item.status == "pending":
                    break
                if item.queue_key != row.queue_key:
                    continue
                if item.status != "ready" or item.filter_revision != channel.revision:
                    break
                if len(batch) >= turn.policy.batch_max_messages or (
                    batch and not turn.policy.debounce_seconds
                ):
                    break
                following = InboundMessage.model_validate(
                    self.store.cipher.decrypt_payload(item.encrypted_payload, key_id=item.key_id)
                )
                user_id, route = resolve_route(
                    session, turn.channel_id, item.identity_id, following
                )
                if (item.user_id, item.route_id, item.route_revision) != (
                    user_id,
                    route.id,
                    route.revision,
                ):
                    raise ConflictError("An admitted message account binding or route changed")
                if user_id != turn.user_id or (
                    route.target_kind,
                    route.target_id,
                    route.output_mode,
                ) != (turn.route.target_kind, turn.route.target_id, turn.route.output_mode):
                    if not batch:
                        raise ConflictError("Conversation queue authorization changed")
                    break
                if not turn.policy.activates(following):
                    break
                part = BatchMessage(
                    inbox_id=item.id, identity_id=item.identity_id, route=route, message=following
                )
                size = len(canonical_json(part.model_dump(mode="json")).encode())
                if size > 1_000_000:
                    raise InvalidArgumentError("An individual message exceeds the batch size limit")
                if batch and total + size > 1_000_000:
                    break
                total += size
                batch.append(part)
                if item.id != row.id:
                    item.status = "batched"
            if not batch:
                return None
            context = (
                self.store.cipher.decrypt_payload(row.encrypted_context)
                if row.encrypted_context
                else {}
            )
            context["batch"] = [item.model_dump(mode="json") for item in batch]
            row.encrypted_context = self.store.cipher.encrypt_payload(context).ciphertext
            row.status = "dispatching"
            return turn.model_copy(update={"batch": batch})

    def ignore(self, turn: GatewayTurn) -> None:
        with self.store.sessions.begin() as session:
            authorize_turn(session, turn)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.run_id is not None:
                raise ConflictError("A submitted event cannot be ignored")
            row.status = "completed"

    def submitted(self, turn: GatewayTurn, run_id: UUID) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            row.status, row.run_id = "submitted", run_id
            row.route_id, row.user_id, row.route_revision = (
                turn.route.id,
                turn.user_id,
                turn.route.revision,
            )

    def fail(self, inbox_id: UUID, owner: str, error: str) -> None:
        with self.store.sessions.begin() as session:
            channel_id = session.scalar(
                select(GatewayInbox.channel_id).where(GatewayInbox.id == inbox_id)
            )
            if channel_id is None:
                return
            require_lease(session, channel_id, owner)
            row = session.get(GatewayInbox, inbox_id, with_for_update=True)
            assert row is not None
            if row.status != "completed":
                row.status, row.error_code = "blocked", error
