from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import EgressUrlPolicy, validate_egress_url
from backend.app.webhooks.constants import WEBHOOK_URL_POLICY
from backend.app.webhooks.models import WebhookSubscription
from backend.app.webhooks.utils import _normalized_event_types


class WebhookSubscriptionService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
        egress_policy: EgressUrlPolicy = WEBHOOK_URL_POLICY,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._egress_policy = egress_policy

    def create(
        self,
        *,
        workspace_id: UUID,
        created_by_user_id: UUID,
        name: str,
        target_url: str,
        event_types: list[str],
        signing_secret: str,
    ) -> WebhookSubscription:
        encrypted = self._secret_service.encrypt_payload({"signing_secret": signing_secret})
        subscription = WebhookSubscription(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            name=name,
            target_url=validate_egress_url(target_url, policy=self._egress_policy),
            event_types=_normalized_event_types(event_types),
            encrypted_signing_secret=encrypted.ciphertext,
            signing_secret_fingerprint=encrypted.fingerprint,
            encryption_key_id=encrypted.key_id,
            status="active",
        )
        self._session.add(subscription)
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def list(
        self,
        *,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[WebhookSubscription], int]:
        statement = select(WebhookSubscription).where(
            WebhookSubscription.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(WebhookSubscription.status == status)
        statement = statement.order_by(
            WebhookSubscription.created_at.desc(),
            WebhookSubscription.id.desc(),
        )
        return page_scalars(self._session, statement, page)

    def update(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        name: str | None = None,
        target_url: str | None = None,
        event_types: list[str] | None = None,
    ) -> WebhookSubscription:
        subscription = self._require(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
        )
        if name is not None:
            subscription.name = name
        if target_url is not None:
            subscription.target_url = validate_egress_url(
                target_url,
                policy=self._egress_policy,
            )
        if event_types is not None:
            subscription.event_types = _normalized_event_types(event_types)
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def rotate_signing_secret(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        signing_secret: str,
    ) -> WebhookSubscription:
        subscription = self._require(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
        )
        encrypted = self._secret_service.encrypt_payload({"signing_secret": signing_secret})
        subscription.encrypted_signing_secret = encrypted.ciphertext
        subscription.signing_secret_fingerprint = encrypted.fingerprint
        subscription.encryption_key_id = encrypted.key_id
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def disable(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
    ) -> WebhookSubscription:
        subscription = self._require(
            workspace_id=workspace_id,
            subscription_id=subscription_id,
        )
        if subscription.status != "disabled":
            subscription.status = "disabled"
            subscription.disabled_at = datetime.now(UTC)
        self._session.commit()
        self._session.refresh(subscription)
        return subscription

    def _require(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
    ) -> WebhookSubscription:
        subscription = self._session.scalar(
            select(WebhookSubscription).where(
                WebhookSubscription.workspace_id == workspace_id,
                WebhookSubscription.id == subscription_id,
            )
        )
        if subscription is None:
            raise ValueError("Webhook subscription not found")
        return subscription
