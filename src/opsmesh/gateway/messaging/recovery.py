"""Explicit audited recovery; uncertain deliveries never retry automatically."""

from uuid import UUID

from opsmesh.gateway.contracts import DeliveryResolution
from opsmesh.gateway.storage.models import GatewayDelivery, GatewayInbox
from opsmesh.gateway.storage.store import GatewayStore
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError


def retry_inbox(store: GatewayStore, channel_id: UUID, inbox_id: UUID, actor_id: UUID) -> None:
    with store.sessions.begin() as session:
        row = session.get(GatewayInbox, inbox_id, with_for_update=True)
        if row is None or row.channel_id != channel_id:
            raise NotFoundError("Inbox event does not exist")
        if row.status != "blocked":
            raise ConflictError("Only blocked inbox events can be retried")
        if row.run_id is None:
            context = (
                store.cipher.decrypt_payload(row.encrypted_context) if row.encrypted_context else {}
            )
            batch = context.get("batch", [])
            for item in batch if isinstance(batch, list) else []:
                if not isinstance(item, dict):
                    raise ConflictError("Invalid stored gateway batch")
                child = session.get(GatewayInbox, UUID(str(item["inbox_id"])), with_for_update=True)
                if child is not None and child.id != row.id and child.status == "batched":
                    child.status = "pending"
                    child.route_id, child.route_revision, child.user_id = None, None, None
                    child.queue_key, child.filter_revision, child.encrypted_context = (
                        None,
                        None,
                        None,
                    )
                    child.filter_attempts, child.filter_retry_at = 0, None
            row.route_id, row.route_revision, row.user_id = None, None, None
            row.queue_key, row.filter_revision, row.encrypted_context = None, None, None
        row.status = "submitted" if row.run_id else "pending"
        row.error_code = None
        row.filter_attempts, row.filter_retry_at = 0, None
        store.audit(session, "inbox.retry", actor_id, inbox_id)


def resolve_delivery(
    store: GatewayStore,
    channel_id: UUID,
    delivery_id: UUID,
    actor_id: UUID,
    request: DeliveryResolution,
) -> None:
    with store.sessions.begin() as session:
        row = session.get(GatewayDelivery, delivery_id, with_for_update=True)
        if row is None or row.channel_id != channel_id:
            raise NotFoundError("Delivery does not exist")
        if row.status not in {"uncertain", "failed", "blocked"}:
            raise ConflictError("Delivery is not awaiting administrative recovery")
        if request.action == "retry":
            if row.status == "uncertain" and not request.accept_duplicate_risk:
                raise InvalidArgumentError("Retrying an uncertain delivery may duplicate it")
            row.status, row.error_code = "pending", None
        else:
            if not request.remote_id:
                raise InvalidArgumentError("Acknowledgement requires a verified remote message ID")
            row.status, row.remote_id, row.error_code = "sent", request.remote_id, None
        store.audit(session, "delivery." + request.action, actor_id, delivery_id)
