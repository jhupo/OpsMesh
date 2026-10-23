"""Durable deliveries: a crash during sending becomes uncertain, never blindly resent."""

from uuid import UUID, uuid4

from opsmesh_sdk.channels import ContentPart, OutboundMessage
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.leases import require_lease
from opsmesh.gateway.models import GatewayDelivery, GatewayInbox
from opsmesh.gateway.store import GatewayStore


class Outbox:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def enqueue(self, session: Session, turn: GatewayTurn, content: list[ContentPart]) -> None:
        for ordinal, part in enumerate(content):
            delivery_id = uuid4()
            value = OutboundMessage(
                delivery_id=delivery_id,
                address={**turn.message.reply_address, "msg_seq": ordinal + 1},
                content=[part],
            )
            encrypted = self.store.cipher.encrypt_payload(value.model_dump(mode="json"))
            session.add(
                GatewayDelivery(
                    id=delivery_id,
                    channel_id=turn.channel_id,
                    inbox_id=turn.inbox_id,
                    ordinal=ordinal,
                    encrypted_payload=encrypted.ciphertext,
                    key_id=encrypted.key_id,
                )
            )

    def complete(self, turn: GatewayTurn, content: list[ContentPart]) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.status == "completed":
                return
            row.route_id, row.user_id, row.route_revision = (
                turn.route.id,
                turn.user_id,
                turn.route.revision,
            )
            self.enqueue(session, turn, content)
            row.status = "completed"

    def recover(self, channel_id: UUID, owner: str) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            for row in session.scalars(
                select(GatewayDelivery)
                .where(
                    GatewayDelivery.channel_id == channel_id, GatewayDelivery.status == "sending"
                )
                .with_for_update()
            ):
                row.status, row.error_code = "uncertain", "worker_lost_during_delivery"

    def next(self, channel_id: UUID, owner: str) -> OutboundMessage | None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            earlier = aliased(GatewayDelivery)
            row = session.scalar(
                select(GatewayDelivery)
                .where(
                    GatewayDelivery.channel_id == channel_id,
                    GatewayDelivery.status == "pending",
                    ~select(earlier.id)
                    .where(
                        earlier.inbox_id == GatewayDelivery.inbox_id,
                        earlier.ordinal < GatewayDelivery.ordinal,
                        earlier.status != "sent",
                    )
                    .exists(),
                )
                .order_by(GatewayDelivery.created_at, GatewayDelivery.ordinal)
                .limit(1)
                .with_for_update()
            )
            if row is None:
                return None
            row.status, row.attempts = "sending", row.attempts + 1
            return OutboundMessage.model_validate(
                self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )

    def finish(
        self,
        channel_id: UUID,
        owner: str,
        delivery_id: UUID,
        status: str,
        remote_id: str = "",
        error: str | None = None,
    ) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            row = session.get(GatewayDelivery, delivery_id, with_for_update=True)
            if row and row.status == "sending":
                if status == "pending" and row.attempts >= 8:
                    status, error = "failed", "retry_limit_exceeded"
                row.status, row.remote_id, row.error_code = status, remote_id, error

    def delivery_turn(self, delivery_id: UUID, owner: str) -> GatewayTurn | None:
        from opsmesh.gateway.inbox import Inbox

        with self.store.sessions() as session:
            row = session.get(GatewayDelivery, delivery_id)
            assert row is not None
            inbox_id = row.inbox_id
        return Inbox(self.store).turn(inbox_id, owner)[0] if inbox_id else None
