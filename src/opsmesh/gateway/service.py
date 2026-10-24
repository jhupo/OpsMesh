from dataclasses import asdict
from typing import Any

from opsmesh_sdk import InvocationSource
from opsmesh_sdk.channels import ChannelEvent
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.infra.encryption import SecretCipher
from opsmesh.shared.errors import DomainError
from opsmesh.tasks.service import submit
from opsmesh.users.service import identity

from .models import ChannelConfiguration, ChannelEventRecord, ChannelIdentity, InboundMessage
from .operations import bounded_json


def receive(
    db: Session, cipher: SecretCipher, channel_id: str, message: ChannelEvent
) -> str | None:
    channel = db.get(ChannelConfiguration, channel_id, with_for_update=True)
    if channel is None or not channel.enabled:
        return None
    existing = db.scalar(
        select(ChannelEventRecord).where(
            ChannelEventRecord.channel_id == channel_id,
            ChannelEventRecord.external_event_id == message.id,
        )
    )
    if existing is not None:
        return existing.task_id
    public_event = asdict(message)
    public_event.pop("reply_target")
    bounded_json(public_event)
    event = ChannelEventRecord(
        channel_id=channel_id,
        external_event_id=message.id,
        event_type=message.type,
        encrypted_event=cipher.encrypt(public_event),
    )
    db.add(event)
    if not message.is_message or not message.external_user_id or not message.conversation_id:
        return None
    binding = db.get(ChannelIdentity, (channel_id, message.external_user_id))
    if binding is None:
        event.status = "unbound"
        return None
    source = InvocationSource(
        channel_id=channel_id,
        conversation_id=message.conversation_id,
        message_id=message.id,
        external_user_id=message.external_user_id,
        external_role=message.external_role,
        reply_scope=message.scope,
    )
    try:
        with db.begin_nested():
            user = identity(db, binding.user_id)
            task = submit(
                db,
                cipher,
                user,
                channel.agent_id,
                message.text or f"[{message.type}: attached content]",
                metadata={"channel_event": public_event},
                source=source,
                idempotency_key=f"channel:{channel_id}:{message.id}",
            )
    except DomainError as exc:
        event.status, event.error_code = "rejected", exc.code
        return None
    event.user_id, event.task_id, event.status = user.user_id, task.id, "submitted"
    # Routing secrets stay encrypted in host storage and never enter plugin context.
    source_data: dict[str, Any] = asdict(source)
    source_data["target"] = message.reply_target
    task.encrypted_source = cipher.encrypt(source_data)
    db.add(
        InboundMessage(
            channel_id=channel_id,
            external_message_id=message.id,
            user_id=user.user_id,
            task_id=task.id,
        )
    )
    return task.id
