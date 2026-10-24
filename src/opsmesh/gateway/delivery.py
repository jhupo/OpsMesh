import asyncio
import logging
from dataclasses import asdict

from opsmesh_sdk.channels import ChannelAdapter, OperationRejected, OutgoingMessage
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.infra.database import new_id, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.shared.errors import ConflictError
from opsmesh.tasks.models import Task

from .models import ChannelConfiguration, InboundMessage, OutboundDelivery
from .operations import bounded_json

logger = logging.getLogger(__name__)


def enqueue(
    db: Session,
    cipher: SecretCipher,
    task: Task,
    key: str,
    text: str,
    *,
    private: bool = False,
    content: list[dict] | None = None,
) -> str | None:
    source = cipher.decrypt(task.encrypted_source)
    if not source.get("channel_id"):
        return None
    # Group conversations have no safe implicit private-message route.
    if private and source.get("reply_scope") != "private":
        text = "A task requires your input. Open your OpsMesh interactions inbox."
        content = None
    existing = db.scalar(
        select(OutboundDelivery).where(
            OutboundDelivery.task_id == task.id, OutboundDelivery.deduplication_key == key
        )
    )
    if existing:
        previous = cipher.decrypt(existing.encrypted_message)
        if previous["text"] != text or previous.get("content", []) != (content or []):
            raise ConflictError("Reply key was used for different content")
        return existing.id
    sequence = db.scalar(
        update(InboundMessage)
        .where(
            InboundMessage.channel_id == source["channel_id"],
            InboundMessage.external_message_id == source["message_id"],
        )
        .values(reply_sequence=InboundMessage.reply_sequence + 1)
        .returning(InboundMessage.reply_sequence)
    )
    if sequence is None:
        raise ValueError("Channel task has no durable incoming message")
    message = OutgoingMessage(
        id=new_id(),
        conversation_id=source["conversation_id"],
        text=text,
        reply_to=source.get("message_id"),
        scope=source.get("reply_scope", "private"),
        target=source.get("target"),
        sequence=sequence,
        content=content or [],
    )
    bounded_json(asdict(message))
    delivery = OutboundDelivery(
        channel_id=source["channel_id"],
        task_id=task.id,
        deduplication_key=key,
        encrypted_message=cipher.encrypt(asdict(message)),
    )
    db.add(delivery)
    db.flush()
    return delivery.id


async def deliver(
    sessions: sessionmaker,
    cipher: SecretCipher,
    channel_id: str,
    adapter: ChannelAdapter,
    worker_id: str,
) -> None:
    while True:
        await adapter.ready.wait()
        with sessions.begin() as db:
            channel = db.get(ChannelConfiguration, channel_id)
            if (
                channel is None
                or not channel.enabled
                or channel.worker_id != worker_id
                or channel.lease_until is None
                or channel.lease_until <= now()
            ):
                return
            delivery = db.scalar(
                select(OutboundDelivery)
                .where(
                    OutboundDelivery.channel_id == channel_id,
                    OutboundDelivery.status == "pending",
                )
                .order_by(OutboundDelivery.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if delivery is not None:
                delivery.status, delivery.attempts = "sending", delivery.attempts + 1
                delivery_id, payload = (
                    delivery.id,
                    cipher.decrypt(delivery.encrypted_message),
                )
            else:
                delivery_id, payload = None, None
        if delivery_id is None:
            await asyncio.sleep(1)
            continue
        try:
            async with asyncio.timeout(30):
                receipt = await adapter.send(OutgoingMessage(**payload))
            bounded_json(receipt)
            status, code = "delivered", None
        except asyncio.CancelledError:
            raise
        except OperationRejected as exc:
            status, code = "failed", exc.code
        except Exception as exc:
            status, code = "uncertain", type(exc).__name__
            logger.warning("gateway_delivery_uncertain")
        with sessions.begin() as db:
            delivery = db.get(OutboundDelivery, delivery_id, with_for_update=True)
            if delivery is not None and delivery.status == "sending":
                delivery.status, delivery.error_code = status, code
                if status == "delivered":
                    delivery.encrypted_receipt = cipher.encrypt({"value": receipt})
