"""Durable, idempotent channel ingress and delivery transitions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.channels.contracts import ChannelFlowStarter
from opsmesh.channels.models import ChannelAccount, ChannelDelivery, ChannelInbox, ChannelRoute
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json

INBOX_STATES = {"received", "routed", "rejected", "needs_attention"}
DELIVERY_STATES = {"pending", "sending", "sent", "failed", "needs_attention", "cancelled"}


def unsettled_delivery_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ChannelDelivery)
            .where(ChannelDelivery.status.in_(("sending", "needs_attention")))
        )
        or 0
    )


def _mapped_input(payload: dict[str, object], mapping: dict[str, str]) -> dict[str, object]:
    """Apply the route's bounded dotted-path mapping without evaluating code."""
    if not mapping:
        return {"event": payload}
    result: dict[str, object] = {}
    for target, source in mapping.items():
        value: Any = payload
        for part in source.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ConflictError(f"Channel input mapping source is missing: {source}")
            value = value[part]
        result[target] = value
    return result


def route_inbox(
    session: Session,
    *,
    inbox: ChannelInbox,
    actor: CurrentUser,
    cipher: SecretCipher,
    start_flow: ChannelFlowStarter | None,
) -> ChannelInbox:
    """Start the configured fixed flow after durable inbox persistence.

    ``start_flow`` is assembled by bootstrap; channels never imports the flow
    implementation or starts a second execution engine. Missing routes are a
    durable rejection, while duplicate deliveries reuse the existing run.
    """
    route = session.scalar(
        select(ChannelRoute).where(
            ChannelRoute.account_id == inbox.account_id,
            ChannelRoute.conversation_key == inbox.conversation_key,
            ChannelRoute.enabled.is_(True),
        )
    )
    if route is None:
        inbox.status = "rejected"
        inbox.failure_code = "no_route"
        return inbox
    if inbox.flow_run_id is not None:
        inbox.status = "routed"
        return inbox
    if start_flow is None:
        inbox.status = "needs_attention"
        inbox.failure_code = "flow_dispatch_unavailable"
        return inbox
    payload = cipher.decrypt_payload(inbox.payload_ciphertext)
    run_id = start_flow(
        session,
        actor=actor,
        configuration_id=route.configuration_id,
        revision_id=route.flow_revision_id,
        input=_mapped_input(payload, route.input_mapping),
        idempotency_key=f"channel:{inbox.id}",
    )
    inbox.flow_run_id = run_id
    inbox.status = "routed"
    inbox.failure_code = None
    return inbox


def persist_inbox(
    session: Session,
    *,
    account_id: UUID,
    epoch: int,
    external_message_id: str,
    conversation_key: str,
    payload: dict[str, object],
    cipher: SecretCipher,
) -> tuple[ChannelInbox, bool]:
    """Persist a verified vendor event before its protocol adapter acknowledges it."""
    session.flush()
    account = session.scalar(
        select(ChannelAccount)
        .where(ChannelAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise NotFoundError("Channel account not found")
    if account.status != "enabled" or account.epoch != epoch:
        raise ConflictError("Channel connection epoch is no longer active", code="channel_epoch")
    if len(canonical_json(payload).encode()) > 1_048_576:
        raise InvalidArgumentError("The channel payload exceeds the size limit")
    encoded = cipher.encrypt_payload(payload)
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    existing = session.scalar(
        select(ChannelInbox).where(
            ChannelInbox.account_id == account_id,
            ChannelInbox.external_message_id == external_message_id,
        )
    )
    if existing is not None:
        if existing.payload_digest != digest or existing.conversation_key != conversation_key:
            raise ConflictError("Channel message ID was reused with different content")
        return existing, False
    inbox = ChannelInbox(
        account_id=account_id,
        connection_epoch=epoch,
        external_message_id=external_message_id,
        conversation_key=conversation_key,
        payload_ciphertext=encoded.ciphertext,
        payload_key_id=encoded.key_id,
        payload_digest=digest,
        received_at=datetime.now(UTC),
    )
    session.add(inbox)
    session.flush()
    return inbox, True


def reserve_delivery(
    session: Session,
    *,
    account_id: UUID,
    idempotency_key: str,
    conversation_key: str,
    payload: dict[str, object],
    cipher: SecretCipher,
) -> ChannelDelivery:
    """Record outbound intent before a protocol package performs network I/O."""
    session.flush()
    account = session.scalar(
        select(ChannelAccount)
        .where(ChannelAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None or account.status != "enabled":
        raise NotFoundError("Enabled channel account not found")
    if len(canonical_json(payload).encode()) > 1_048_576:
        raise InvalidArgumentError("The channel payload exceeds 1 MiB")
    encoded = cipher.encrypt_payload(payload)
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    existing = session.scalar(
        select(ChannelDelivery).where(
            ChannelDelivery.account_id == account_id,
            ChannelDelivery.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.payload_digest != digest or existing.conversation_key != conversation_key:
            raise ConflictError("Delivery idempotency key was reused with different content")
        return existing
    delivery = ChannelDelivery(
        account_id=account_id,
        connection_epoch=account.epoch,
        idempotency_key=idempotency_key,
        conversation_key=conversation_key,
        payload_ciphertext=encoded.ciphertext,
        payload_key_id=encoded.key_id,
        payload_digest=digest,
    )
    session.add(delivery)
    session.flush()
    return delivery


def record_delivery_result(
    session: Session,
    *,
    delivery_id: UUID,
    status: str,
    external_message_id: str | None = None,
    failure_code: str | None = None,
) -> ChannelDelivery:
    """Record a confirmed result; an unknown remote effect requires reconciliation."""
    if status not in {"sent", "failed", "needs_attention"}:
        raise ValueError("Invalid channel delivery result state")
    if status == "sent" and not external_message_id:
        raise InvalidArgumentError("A confirmed send requires its external message ID")
    session.flush()
    delivery = session.scalar(
        select(ChannelDelivery)
        .where(ChannelDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        raise NotFoundError("Channel delivery not found")
    if delivery.status in {"sent", "failed", "cancelled", "needs_attention"}:
        if (
            delivery.status != status
            or delivery.external_message_id != external_message_id
            or delivery.failure_code != failure_code
        ):
            raise ConflictError("A terminal channel delivery cannot change state")
        return delivery
    if delivery.status != "sending":
        raise ConflictError("Only a claimed delivery can record a send result")
    delivery.status = status
    delivery.external_message_id = external_message_id
    delivery.failure_code = failure_code
    return delivery


def claim_delivery(session: Session, *, delivery_id: UUID) -> ChannelDelivery:
    """Claim one pending intent, rejecting sends from a revoked connection epoch."""
    session.flush()
    delivery = session.scalar(
        select(ChannelDelivery)
        .where(ChannelDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        raise NotFoundError("Channel delivery not found")
    if delivery.status != "pending":
        raise ConflictError("Channel delivery is not pending")
    account = session.scalar(
        select(ChannelAccount)
        .where(ChannelAccount.id == delivery.account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None or account.status != "enabled" or account.epoch != delivery.connection_epoch:
        delivery.status = "needs_attention"
        delivery.failure_code = "connection_epoch_changed"
        return delivery
    delivery.status = "sending"
    delivery.attempts += 1
    return delivery


def reconcile_delivery(
    session: Session,
    *,
    delivery_id: UUID,
    status: str,
    evidence: str,
    actor_id: UUID,
    external_message_id: str | None = None,
) -> tuple[ChannelDelivery, bool]:
    """Preserve interrupted sends for inspection, then confirm their existing effect."""
    if status not in {"needs_attention", "sent", "failed"} or not evidence.strip():
        raise InvalidArgumentError("Reconciliation requires a valid state and evidence")
    if status == "sent" and not external_message_id:
        raise InvalidArgumentError("A confirmed send requires its external message ID")
    if status == "needs_attention" and external_message_id is not None:
        raise InvalidArgumentError("An interrupted send has no confirmed external message ID")
    session.flush()
    delivery = session.scalar(
        select(ChannelDelivery)
        .where(ChannelDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        raise NotFoundError("Channel delivery not found")
    if (
        delivery.status == status
        and delivery.external_message_id == external_message_id
        and delivery.reconciliation_note == evidence
        and delivery.reconciled_by == actor_id
    ):
        return delivery, False
    expected = "sending" if status == "needs_attention" else "needs_attention"
    if delivery.status != expected:
        raise ConflictError("The delivery cannot accept this reconciliation")
    delivery.status = status
    delivery.external_message_id = external_message_id
    delivery.failure_code = "send_interrupted" if status == "needs_attention" else None
    delivery.reconciliation_note, delivery.reconciled_by = evidence, actor_id
    return delivery, True


def change_connection(
    session: Session,
    *,
    account_id: UUID,
    expected_epoch: int,
    enabled: bool,
) -> ChannelAccount:
    session.flush()
    account = session.scalar(
        select(ChannelAccount)
        .where(ChannelAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise NotFoundError("Channel account not found")
    if account.epoch != expected_epoch:
        raise ConflictError("The connection epoch changed", code="channel_epoch")
    account.epoch += 1
    account.status = "enabled" if enabled else "disabled"
    return account
