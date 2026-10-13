"""Send a platform-owned reply once and persist externally observed receipts."""

import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid5

import httpx
from opentelemetry import propagate
from opsmesh_plugin_sdk.messaging.contracts import ChannelReplyRequest, ReplyReceiptRequest
from opsmesh_plugin_sdk.messaging.webhooks import signed_headers
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.policy import require_plugin_resource
from opsmesh.governance.audit.service import AuditService
from opsmesh.messaging.channels.models import ChannelConnection, ChannelDelivery
from opsmesh.orchestration.automations.authorization import (
    require_automation_principal,
    require_event_principal,
)
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.models import Automation, AutomationEvent
from opsmesh.runtime.temporal.outbox import stage_signal
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.egress import EgressURLPolicy, validate_egress_url
from opsmesh.shared.security.secrets import SecretEncryptionService

TERMINAL_DELIVERY_STATUSES = frozenset({"delivered", "failed", "unknown"})


def require_delivery(session: Session, workspace_id: UUID, delivery_id: UUID) -> ChannelDelivery:
    delivery = session.scalar(
        select(ChannelDelivery)
        .where(
            ChannelDelivery.workspace_id == workspace_id,
            ChannelDelivery.id == delivery_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        raise ValueError("Channel delivery not found")
    return delivery


def record_receipt(
    session: Session, delivery: ChannelDelivery, receipt: ReplyReceiptRequest
) -> None:
    """Persist evidence and its wake-up in one transaction; never re-execute a Task."""
    if receipt.delivery_id != delivery.id:
        raise ValueError("Receipt identifies another delivery")
    if delivery.status == "pending":
        raise DomainError(
            "Delivery has not been attempted", code="channel_receipt_conflict", status_code=409
        )
    if (delivery.status, delivery.provider_message_id, delivery.error_code) == (
        receipt.status,
        receipt.provider_message_id,
        receipt.error_code,
    ):
        return
    if delivery.status in {"delivered", "failed"}:
        raise DomainError(
            "Delivery already has a different receipt",
            code="channel_receipt_conflict",
            status_code=409,
        )
    delivery.status = receipt.status
    delivery.provider_message_id = receipt.provider_message_id
    delivery.error_code = receipt.error_code
    AuditService(session).record_system_action(
        workspace_id=delivery.workspace_id,
        action=f"channel.delivery.{receipt.status}",
        target_type="channel_delivery",
        target_id=delivery.id,
        metadata={"event_id": str(delivery.event_id), "connection_id": str(delivery.connection_id)},
    )
    stage_signal(
        session,
        dispatch_id=uuid5(delivery.id, f"receipt:{receipt.status}"),
        workspace_id=delivery.workspace_id,
        workflow_id=f"opsmesh:{delivery.workspace_id}:reply_delivery:{delivery.event_id}",
        signal_name="wake",
        resource_id=delivery.event_id,
    )


class ChannelDeliveryService:
    def __init__(
        self,
        session: Session,
        secrets: SecretEncryptionService,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.session = session
        self.secrets = secrets
        self.transport = transport

    def send(self, workspace_id: UUID, delivery_id: UUID) -> str:
        delivery = require_delivery(self.session, workspace_id, delivery_id)
        if delivery.status in TERMINAL_DELIVERY_STATUSES:
            return delivery.status
        if delivery.status == "sending":
            # A completed HTTP 202 is safe to wait for. An interrupted request is ambiguous.
            if delivery.accepted_at is None:
                return self.interrupted(workspace_id, delivery_id)
            return "sending"
        try:
            connection = self._authorize(delivery)
            endpoint = validate_egress_url(
                connection.endpoint_url,
                policy=EgressURLPolicy(
                    allowed_schemes=frozenset({"https"}),
                    allow_unresolved_hosts=False,
                ),
            )
            secret = self.secrets.decrypt_payload(connection.encrypted_signing_secret).get(
                "signing_secret"
            )
            if not isinstance(secret, str) or not secret:
                raise ValueError("Channel signing credential unavailable")
            request = ChannelReplyRequest(
                delivery_id=delivery.id,
                connection_id=connection.id,
                event_id=delivery.event_id,
                reply=delivery.payload,
            )
            body = json.dumps(
                request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
            headers = signed_headers(
                body=body,
                secret=secret,
                timestamp=str(int(time.time())),
                event_id=str(delivery.event_id),
                event_type="channel.reply",
                delivery_attempt_id=str(delivery.id),
            )
            headers["Idempotency-Key"] = str(delivery.id)
            propagate.inject(headers)
        except (ValueError, DomainError):
            return self._fail_before_send(delivery, "channel_authorization_revoked")
        # Durable claim precedes any external write. No transaction is held during HTTP.
        delivery.status = "sending"
        self.session.commit()
        try:
            with (
                httpx.Client(
                    timeout=15, follow_redirects=False, trust_env=False, transport=self.transport
                ) as client,
                client.stream("POST", endpoint, content=body, headers=headers) as response,
            ):
                if response.status_code == 202:
                    delivery = require_delivery(self.session, workspace_id, delivery_id)
                    if delivery.status not in TERMINAL_DELIVERY_STATUSES:
                        delivery.accepted_at = datetime.now(UTC)
                    self.session.commit()
                    return delivery.status
                if response.status_code != 200:
                    raise ValueError("Connector did not provide an authoritative send receipt")
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=8193):
                    content.extend(chunk)
                    if len(content) > 8192:
                        raise ValueError("Connector receipt exceeds limit")
                receipt = ReplyReceiptRequest.model_validate_json(bytes(content))
        except (httpx.HTTPError, ValueError):
            return self.interrupted(workspace_id, delivery_id)
        delivery = require_delivery(self.session, workspace_id, delivery_id)
        record_receipt(self.session, delivery, receipt)
        self.session.commit()
        return delivery.status

    def interrupted(self, workspace_id: UUID, delivery_id: UUID) -> str:
        delivery = require_delivery(self.session, workspace_id, delivery_id)
        if delivery.status == "pending":
            return self._fail_before_send(delivery, "channel_send_not_started")
        if delivery.status not in TERMINAL_DELIVERY_STATUSES:
            record_receipt(
                self.session,
                delivery,
                ReplyReceiptRequest(
                    delivery_id=delivery.id,
                    status="unknown",
                    error_code="channel_send_outcome_unknown",
                ),
            )
        self.session.commit()
        return delivery.status

    def _fail_before_send(self, delivery: ChannelDelivery, error_code: str) -> str:
        delivery.status = "failed"
        delivery.error_code = error_code
        AuditService(self.session).record_system_action(
            workspace_id=delivery.workspace_id,
            action="channel.delivery.failed",
            target_type="channel_delivery",
            target_id=delivery.id,
            metadata={"connection_id": str(delivery.connection_id), "reason": error_code},
        )
        self.session.commit()
        return delivery.status

    def _authorize(self, delivery: ChannelDelivery) -> ChannelConnection:
        event = self.session.scalar(
            select(AutomationEvent).where(
                AutomationEvent.workspace_id == delivery.workspace_id,
                AutomationEvent.id == delivery.event_id,
            )
        )
        if event is None:
            raise ValueError("Delivery event missing")
        automation = self.session.scalar(
            select(Automation).where(
                Automation.workspace_id == delivery.workspace_id,
                Automation.id == event.automation_id,
                Automation.status == "active",
            )
        )
        connection = self.session.scalar(
            select(ChannelConnection).where(
                ChannelConnection.workspace_id == delivery.workspace_id,
                ChannelConnection.id == delivery.connection_id,
                ChannelConnection.status == "active",
            )
        )
        if (
            automation is None
            or connection is None
            or connection.configuration_revision != delivery.connection_revision
        ):
            raise ValueError("Delivery target changed or disabled")
        config = AutomationConfiguration.model_validate(automation.configuration)
        if event.source_connection_id is not None:
            source = self.session.scalar(
                select(ChannelConnection).where(
                    ChannelConnection.workspace_id == delivery.workspace_id,
                    ChannelConnection.id == event.source_connection_id,
                    ChannelConnection.status == "active",
                )
            )
            if source is None or config.channel_connection_id != source.id:
                raise ValueError("Channel source disabled or changed")
            require_plugin_resource(
                self.session,
                delivery.workspace_id,
                "channel",
                source.id,
                required_feature="receive",
            )
        if config.reply_connection_id != connection.id:
            raise ValueError("Reply authorization changed")
        if (
            config.trigger_type == "message"
            and event.input_payload.get("sender_id") not in config.allowed_senders
        ):
            raise ValueError("Sender permission revoked")
        require_automation_principal(self.session, automation)
        require_event_principal(self.session, automation, event)
        require_plugin_resource(
            self.session, delivery.workspace_id, "channel", connection.id, required_feature="send"
        )
        return connection
