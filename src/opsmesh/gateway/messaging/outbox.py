"""Durable deliveries: a crash during sending becomes uncertain, never blindly resent."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from opsmesh_sdk.channels import ContentPart, OutboundMessage
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from opsmesh.gateway.channels.leases import require_lease
from opsmesh.gateway.contracts import GatewayEvents, GatewayTurn
from opsmesh.gateway.storage.models import GatewayChannel, GatewayDelivery, GatewayInbox
from opsmesh.gateway.storage.store import GatewayStore


def reconcile_stream_text(accepted: str, incoming: str) -> str:
    """Keep the accepted replace-mode prefix when the final response is revised."""
    if incoming.startswith(accepted):
        return incoming
    if accepted.startswith(incoming):
        return accepted
    prefix = 0
    for left, right in zip(accepted, incoming, strict=False):
        if left != right:
            break
        prefix += 1
    return (accepted + incoming[prefix:])[:100_000]


class Outbox:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store

    def enqueue(
        self,
        session: Session,
        turn: GatewayTurn,
        content: list[ContentPart],
        *,
        event: Literal["message", "delta", "completed", "typing"] = "message",
        transient: bool = False,
    ) -> list[UUID]:
        first = (
            session.scalar(
                select(func.max(GatewayDelivery.ordinal)).where(
                    GatewayDelivery.inbox_id == turn.inbox_id
                )
            )
            or 0
        ) + 1
        identifiers = []
        stream_index = (
            session.scalar(
                select(func.count())
                .select_from(GatewayDelivery)
                .where(
                    GatewayDelivery.inbox_id == turn.inbox_id,
                    GatewayDelivery.kind.in_(("delta", "completed")),
                )
            )
            or 0
        )
        for ordinal, part in enumerate(content, start=first):
            delivery_id = uuid4()
            value = OutboundMessage(
                delivery_id=delivery_id,
                address={**turn.message.reply_address, "msg_seq": ordinal},
                content=[part],
                event=event,
                stream_id=str(turn.inbox_id),
                stream_index=stream_index + ordinal - first,
            )
            encrypted = self.store.cipher.encrypt_payload(value.model_dump(mode="json"))
            session.add(
                GatewayDelivery(
                    id=delivery_id,
                    channel_id=turn.channel_id,
                    inbox_id=turn.inbox_id,
                    ordinal=ordinal,
                    kind="hint" if transient else event,
                    encrypted_payload=encrypted.ciphertext,
                    key_id=encrypted.key_id,
                )
            )
            identifiers.append(delivery_id)
        session.flush()
        return identifiers

    def stream(
        self, session: Session, turn: GatewayTurn, text: str, *, final: bool = False
    ) -> None:
        # Coalesce only requests that have never been sent to the platform.
        row = session.scalar(
            select(GatewayDelivery)
            .where(
                GatewayDelivery.inbox_id == turn.inbox_id,
                GatewayDelivery.kind == "delta",
                GatewayDelivery.status == "pending",
                GatewayDelivery.attempts == 0,
            )
            .order_by(GatewayDelivery.ordinal)
            .limit(1)
            .with_for_update()
        )
        if row is None:
            self.enqueue(
                session, turn, [ContentPart(text=text)], event="completed" if final else "delta"
            )
            return
        value = OutboundMessage.model_validate(
            self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
        )
        value = value.model_copy(
            update={"content": [ContentPart(text=text)], "event": "completed" if final else "delta"}
        )
        encrypted = self.store.cipher.encrypt_payload(value.model_dump(mode="json"))
        row.encrypted_payload, row.key_id = encrypted.ciphertext, encrypted.key_id
        row.kind = value.event

    def complete(self, turn: GatewayTurn, content: list[ContentPart]) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.status == "completed":
                return
            for hint in session.scalars(
                select(GatewayDelivery)
                .where(
                    GatewayDelivery.inbox_id == turn.inbox_id,
                    GatewayDelivery.kind.in_(("typing", "hint")),
                    GatewayDelivery.status == "pending",
                    GatewayDelivery.attempts == 0,
                )
                .with_for_update()
            ):
                hint.status = "discarded"
            row.route_id, row.user_id, row.route_revision = (
                turn.route.id,
                turn.user_id,
                turn.route.revision,
            )
            context = (
                self.store.cipher.decrypt_payload(row.encrypted_context)
                if row.encrypted_context
                else {}
            )
            if context.get("stream_text") and content:
                self.stream(
                    session,
                    turn,
                    "".join(part.text for part in content if part.type == "text"),
                    final=True,
                )
                self.enqueue(session, turn, [part for part in content if part.type != "text"])
            else:
                self.enqueue(session, turn, content)
            row.status = "completed"
            if (
                turn.policy.group_for(turn.message).clear_after_reply
                and content
                and turn.context_ids
            ):
                for item in session.scalars(
                    select(GatewayInbox)
                    .where(GatewayInbox.id.in_(turn.context_ids), GatewayInbox.status == "passive")
                    .with_for_update()
                ):
                    item.status = "context_consumed"

    def events(self, turn: GatewayTurn, batch: GatewayEvents) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.status != "submitted" or batch.cursor <= row.event_cursor:
                return
            context = (
                self.store.cipher.decrypt_payload(row.encrypted_context)
                if row.encrypted_context
                else {}
            )
            text = str(context.get("stream_text") or "")
            previous_text = text
            summary = str(context.get("summary_text") or "")
            now = datetime.now(UTC).timestamp()
            hint_at = context.get("hint_at")
            previous_hint = float(hint_at) if isinstance(hint_at, (int, float)) else 0
            for event in batch.events:
                if event.sequence <= row.event_cursor:
                    continue
                if (
                    event.kind == "delta"
                    and turn.policy.streaming
                    and turn.route.output_mode == "text"
                    and turn.message.reply_address.get("kind") == "c2c"
                ):
                    text = (text + event.text)[:100_000]
                elif event.kind in {"tool_start", "tool_end"} and turn.policy.show_tool_hints:
                    if now - previous_hint >= turn.policy.tool_hint_throttle_seconds:
                        self.enqueue(
                            session,
                            turn,
                            [ContentPart(text=event.text or "Tool activity.")],
                            transient=True,
                        )
                        previous_hint = now
                elif event.kind == "summary" and turn.policy.show_thinking:
                    summary = (summary + event.text)[:4000]
            raw_summary_at = context.get("summary_at")
            summary_at = float(raw_summary_at) if isinstance(raw_summary_at, (int, float)) else 0
            if summary and now - summary_at >= 6:
                self.enqueue(session, turn, [ContentPart(text=summary)], transient=True)
                summary, summary_at = "", now
            if text != previous_text:
                self.stream(session, turn, text)
            context["stream_text"] = text
            context.update(summary_text=summary, summary_at=summary_at, hint_at=previous_hint)
            row.encrypted_context = self.store.cipher.encrypt_payload(context).ciphertext
            row.event_cursor = batch.cursor

    def progress(self, turn: GatewayTurn) -> None:
        with self.store.sessions.begin() as session:
            require_lease(session, turn.channel_id, turn.lease_owner)
            row = session.get(GatewayInbox, turn.inbox_id, with_for_update=True)
            assert row is not None
            if row.status != "submitted":
                return
            context = (
                self.store.cipher.decrypt_payload(row.encrypted_context)
                if row.encrypted_context
                else {}
            )
            age = (datetime.now(UTC) - row.created_at).total_seconds()
            if (
                turn.policy.progress_after_seconds
                and age >= turn.policy.progress_after_seconds
                and not context.get("progress_sent")
            ):
                self.enqueue(
                    session, turn, [ContentPart(text=turn.policy.progress_text)], transient=True
                )
                context["progress_sent"] = True
            interval = turn.policy.typing_interval_seconds
            raw_previous = context.get("typing_at")
            previous = float(raw_previous) if isinstance(raw_previous, (int, float)) else 0
            now = datetime.now(UTC).timestamp()
            queued_typing = session.scalar(
                select(GatewayDelivery.id)
                .where(
                    GatewayDelivery.inbox_id == turn.inbox_id,
                    GatewayDelivery.kind == "typing",
                    GatewayDelivery.status.in_(("pending", "sending")),
                )
                .limit(1)
            )
            if (
                interval
                and not queued_typing
                and turn.message.reply_address.get("kind") == "c2c"
                and now - previous >= interval
            ):
                self.enqueue(session, turn, [ContentPart(text="")], event="typing")
                context["typing_at"] = now
            row.encrypted_context = self.store.cipher.encrypt_payload(context).ciphertext

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

    def next(
        self, channel_id: UUID, owner: str, *, send_limit: int = 20, send_window_seconds: int = 60
    ) -> OutboundMessage | None:
        with self.store.sessions.begin() as session:
            require_lease(session, channel_id, owner)
            cutoff = datetime.now(UTC) - timedelta(seconds=send_window_seconds)
            channel = session.get(GatewayChannel, channel_id, with_for_update=True)
            if channel is None or not channel.enabled or channel.deleted:
                return None
            if channel.send_window_started is None or channel.send_window_started <= cutoff:
                channel.send_window_started, channel.send_window_count = datetime.now(UTC), 0
            if channel.send_window_count >= send_limit:
                return None
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
                        earlier.status.not_in(("sent", "discarded")),
                    )
                    .exists(),
                )
                .order_by(GatewayDelivery.created_at, GatewayDelivery.ordinal)
                .limit(1)
                .with_for_update()
            )
            if row is None:
                return None
            channel.send_window_count += 1
            row.status, row.attempts = "sending", row.attempts + 1
            message = OutboundMessage.model_validate(
                self.store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )
            if message.event in {"delta", "completed"}:
                previous = session.scalar(
                    select(GatewayDelivery)
                    .where(
                        GatewayDelivery.inbox_id == row.inbox_id,
                        GatewayDelivery.ordinal < row.ordinal,
                        GatewayDelivery.status == "sent",
                        GatewayDelivery.kind.in_(("delta", "completed")),
                    )
                    .order_by(GatewayDelivery.ordinal.desc())
                    .limit(1)
                )
                if previous is not None:
                    accepted = OutboundMessage.model_validate(
                        self.store.cipher.decrypt_payload(
                            previous.encrypted_payload, key_id=previous.key_id
                        )
                    )
                    message = message.model_copy(
                        update={
                            "address": {**message.address, "stream_msg_id": previous.remote_id},
                            "content": [
                                ContentPart(
                                    text=reconcile_stream_text(
                                        accepted.content[0].text, message.content[0].text
                                    )
                                )
                            ],
                        }
                    )
                    encrypted = self.store.cipher.encrypt_payload(message.model_dump(mode="json"))
                    row.encrypted_payload, row.key_id = encrypted.ciphertext, encrypted.key_id
            return message

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
        from opsmesh.gateway.messaging.inbox import Inbox

        with self.store.sessions() as session:
            row = session.get(GatewayDelivery, delivery_id)
            assert row is not None
            inbox_id = row.inbox_id
        return Inbox(self.store).turn(inbox_id, owner)[0] if inbox_id else None
