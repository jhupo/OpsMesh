"""One authorized target per channel; external identity never selects an executor."""

from uuid import UUID

from opsmesh_sdk.channels import InboundMessage
from sqlalchemy.orm import Session

from opsmesh.gateway.channels.leases import require_lease
from opsmesh.gateway.contracts import ChannelTarget, GatewayTurn
from opsmesh.gateway.storage.models import GatewayChannel, GatewayIdentity, GatewayInbox
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, ForbiddenError


def channel_target(
    session: Session,
    store: GatewayStore,
    channel_id: UUID,
    identity_id: UUID,
    message: InboundMessage,
) -> tuple[ChannelTarget, int]:
    channel = session.get(GatewayChannel, channel_id, with_for_update=True, populate_existing=True)
    identity = session.get(GatewayIdentity, identity_id)
    if channel is None or channel.deleted or not channel.enabled:
        raise ForbiddenError("Channel is disabled")
    if (
        identity is None
        or identity.channel_id != channel_id
        or (identity.scope, identity.id_kind, identity.external_id)
        != (message.actor.scope, message.actor.id_kind, message.actor.external_id)
    ):
        raise ForbiddenError("The message identity does not belong to this channel")
    definition = store.definition(channel)
    if definition.target is None:
        raise ForbiddenError("The channel has no execution target")
    if message.conversation.kind == "private":
        allowed = definition.policy.private_enabled
    else:
        allowed = message.conversation.external_id in definition.policy.groups
    if not allowed:
        raise ForbiddenError("The conversation is not enabled on this channel")
    return definition.target, channel.binding_revision


def require_receipt_target(row: GatewayInbox, target: ChannelTarget, revision: int) -> None:
    if (
        row.user_id,
        row.target_kind,
        row.target_id,
        row.binding_revision,
    ) != (target.user_id, target.target_kind, target.target_id, revision):
        raise ConflictError("The channel target changed after receipt")


def authorize_turn(session: Session, store: GatewayStore, turn: GatewayTurn) -> None:
    require_lease(session, turn.channel_id, turn.lease_owner)
    target, revision = channel_target(
        session, store, turn.channel_id, turn.identity_id, turn.message
    )
    if target != turn.target or revision != turn.binding_revision or target.user_id != turn.user_id:
        raise ConflictError("The channel target changed during processing")
    receipt = session.get(GatewayInbox, turn.inbox_id)
    if receipt is None or (receipt.channel_id, receipt.identity_id) != (
        turn.channel_id,
        turn.identity_id,
    ):
        raise ConflictError("The gateway receipt does not match this turn")
    require_receipt_target(receipt, target, revision)
    for item in turn.batch:
        other, other_revision = channel_target(
            session, store, turn.channel_id, item.identity_id, item.message
        )
        if other != target or other_revision != revision:
            raise ConflictError("Batched messages must use the same channel target")
        child = session.get(GatewayInbox, item.inbox_id)
        if child is None or (child.channel_id, child.identity_id, child.conversation_key) != (
            receipt.channel_id,
            item.identity_id,
            receipt.conversation_key,
        ):
            raise ConflictError("A batched receipt does not belong to this conversation")
        require_receipt_target(child, target, revision)
