"""Authorize saved provenance against current channel policy, never model roles."""

from datetime import UTC, datetime

from opsmesh_sdk.channel_events import ConversationEvent
from opsmesh_sdk.channel_management import (
    ManagementRequest,
    MuteMember,
    RecallMessage,
    ReviewJoinRequest,
)
from opsmesh_sdk.channels import InboundMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.gateway.channels.policy import ManagementGrant
from opsmesh.gateway.contracts import ChannelDefinition
from opsmesh.gateway.management.contracts import OperationOrigin, reference_key
from opsmesh.gateway.routing.service import require_receipt_target
from opsmesh.gateway.storage.models import (
    GatewayConversation,
    GatewayConversationEvent,
    GatewayInbox,
)
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.serialization import payload_digest


def authorize_operation(
    session: Session,
    store: GatewayStore,
    channel: ChannelDefinition,
    origin: OperationOrigin,
    request: ManagementRequest,
    *,
    validate_effect: bool = True,
) -> tuple[str | None, frozenset[str], datetime | None]:
    conversation = request.conversation
    group = channel.policy.groups.get(conversation.external_id)
    if not channel.enabled or channel.target is None or group is None:
        raise ForbiddenError("The conversation is not enabled")
    policy = group.management
    if request.action not in policy.actions:
        raise ForbiddenError("The operation is not granted for this conversation")
    if (
        isinstance(request, ReviewJoinRequest)
        and request.blacklist
        and "blacklist" not in policy.actions
    ):
        raise ForbiddenError("Blacklisting is not granted for this conversation")
    key = reference_key(conversation)
    observed = session.get(GatewayConversation, (channel.id, key))
    if observed is None:
        raise ForbiddenError("The conversation has not been observed on this channel")
    grant: ManagementGrant | None = None
    requester_id = None
    roles: frozenset[str] = frozenset()
    source: InboundMessage | None = None
    if origin.kind != "admin":
        if (
            origin.actor_id != channel.target.user_id
            or not origin.resource_id
            or not origin.generation
        ):
            raise ForbiddenError("Management requires trusted plugin provenance")
        grant = policy.grants.get(origin.resource_id)
        if grant is None or request.action not in grant.actions:
            raise ForbiddenError("The plugin has no grant for this operation")
        if (
            isinstance(request, ReviewJoinRequest)
            and request.blacklist
            and "blacklist" not in grant.actions
        ):
            raise ForbiddenError("The plugin has no blacklist grant")
        if origin.kind == "event":
            if origin.resource_id not in channel.policy.event_handlers or not grant.automatic:
                raise ForbiddenError("The event handler has no automatic grant")
            event = session.get(GatewayConversationEvent, origin.source_id)
            if (
                event is None
                or event.channel_id != channel.id
                or event.binding_revision != channel.binding_revision
            ):
                raise ForbiddenError("The event source is no longer authorized")
            value = ConversationEvent.model_validate(
                store.cipher.decrypt_payload(event.encrypted_payload, key_id=event.key_id)["event"]
            )
            if value.conversation != conversation:
                raise ForbiddenError("An event cannot manage another conversation")
            if isinstance(request, RecallMessage):
                raise ForbiddenError("Conversation events cannot recall chat messages")
            if hasattr(request, "member_id") and request.member_id != value.member_id:
                raise ForbiddenError("An event can manage only its subject")
            if isinstance(request, ReviewJoinRequest) and request.request_id != value.request_id:
                raise ForbiddenError("The join request does not match the event")
        else:
            receipt = session.get(GatewayInbox, origin.source_id)
            if (
                receipt is None
                or receipt.channel_id != channel.id
                or receipt.conversation_key != key
            ):
                raise ForbiddenError("The source receipt does not match this conversation")
            require_receipt_target(receipt, channel.target, channel.binding_revision)
            source = InboundMessage.model_validate(
                store.cipher.decrypt_payload(receipt.encrypted_payload, key_id=receipt.key_id)
            )
            if origin.kind == "filter":
                if origin.resource_id not in channel.policy.message_filters or not grant.automatic:
                    raise ForbiddenError("The message filter has no automatic grant")
                if isinstance(request, ReviewJoinRequest):
                    raise ForbiddenError("A chat receipt cannot authorize an automatic join review")
                if isinstance(request, RecallMessage) and request.message_id != source.event_id:
                    raise ForbiddenError("A filter can recall only its original message")
                if hasattr(request, "member_id") and request.member_id != source.actor.external_id:
                    raise ForbiddenError("A filter can manage only the original sender")
            else:
                context = (
                    store.cipher.decrypt_payload(receipt.encrypted_context)
                    if receipt.encrypted_context
                    else {}
                )
                identities = {str(receipt.identity_id)}
                batch = context.get("batch", [])
                if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
                    raise ForbiddenError("Invalid stored message batch")
                for item in batch:
                    assert isinstance(item, dict)
                    identities.add(str(item["identity_id"]))
                if len(identities) != 1:
                    raise ForbiddenError(
                        "A batch with multiple senders cannot authorize management tools"
                    )
                requester_id, roles = source.actor.external_id, grant.roles
                if source.actor.external_role not in roles:
                    raise ForbiddenError("The source sender has no permitted group role")
    if validate_effect and isinstance(request, MuteMember):
        maximum = min(
            policy.max_mute_seconds, grant.max_mute_seconds if grant else policy.max_mute_seconds
        )
        remaining = (request.expires_at - datetime.now(UTC)).total_seconds()
        if not 0 < remaining <= maximum:
            raise ForbiddenError("Mute expiry exceeds the granted duration or has elapsed")
    deadline = None
    if validate_effect and isinstance(request, RecallMessage):
        row = session.scalar(
            select(GatewayInbox).where(
                GatewayInbox.channel_id == channel.id,
                GatewayInbox.conversation_key == key,
                GatewayInbox.event_key
                == payload_digest({"scope": conversation.scope, "event": request.message_id}),
            )
        )
        original = (
            InboundMessage.model_validate(
                store.cipher.decrypt_payload(row.encrypted_payload, key_id=row.key_id)
            )
            if row
            else None
        )
        if original is None or original.occurred_at is None:
            raise ForbiddenError("Recall requires an observed message with a platform timestamp")
        # The adapter supplies its age limit at execution; this is the original timestamp.
        deadline = original.occurred_at
    return requester_id, roles, deadline
