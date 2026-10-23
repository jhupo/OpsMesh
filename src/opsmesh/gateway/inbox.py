"""Acknowledge transport input only after encrypted durable receipt."""

from uuid import UUID, uuid4

from opsmesh_sdk.channels import InboundMessage
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.conversations import Conversations
from opsmesh.gateway.identities import Identities
from opsmesh.gateway.leases import require_lease
from opsmesh.gateway.models import GatewayChannel, GatewayInbox
from opsmesh.gateway.routing import resolve_route
from opsmesh.gateway.store import GatewayStore
from opsmesh.shared.errors import ConflictError
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
                    encrypted_payload=encrypted.ciphertext,
                    key_id=encrypted.key_id,
                    status="pending",
                )
                .on_conflict_do_nothing(
                    index_elements=[GatewayInbox.channel_id, GatewayInbox.event_key]
                )
            )

    def pending(self, channel_id: UUID) -> list[UUID]:
        with self.store.sessions() as session:
            result: list[UUID] = []
            for status in ("pending", "submitted"):
                result.extend(
                    session.scalars(
                        select(GatewayInbox.id)
                        .where(
                            GatewayInbox.channel_id == channel_id,
                            GatewayInbox.status == status,
                        )
                        .order_by(GatewayInbox.created_at, GatewayInbox.id)
                        .limit(50)
                    )
                )
            return result

    def turn(self, inbox_id: UUID, owner: str) -> tuple[GatewayTurn, UUID | None]:
        with self.store.sessions.begin() as session:
            row = session.get(GatewayInbox, inbox_id)
            if row is None:
                raise ConflictError("Inbox event no longer exists")
            require_lease(session, row.channel_id, owner)
            message = InboundMessage.model_validate(
                self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )
            user_id, route = resolve_route(session, row.channel_id, row.identity_id, message)
            if row.route_id and (
                row.user_id != user_id
                or row.route_id != route.id
                or row.route_revision != route.revision
            ):
                raise ConflictError("Account binding or route changed after dispatch")
            return GatewayTurn(
                inbox_id=row.id,
                channel_id=row.channel_id,
                identity_id=row.identity_id,
                user_id=user_id,
                route=route,
                message=message,
                lease_owner=owner,
            ), row.run_id

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
            row = session.get(GatewayInbox, inbox_id, with_for_update=True)
            if row:
                require_lease(session, row.channel_id, owner)
                if row.status != "completed":
                    row.status, row.error_code = "blocked", error
