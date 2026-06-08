from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.capabilities.models import McpCredentialReference
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.models import SecurityEvent
from backend.app.webhooks.models import WebhookSubscription


@dataclass(frozen=True)
class SecretReencryptResourceSummary:
    scanned: int = 0
    reencrypted: int = 0
    skipped_current: int = 0
    failed: int = 0
    from_key_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecretReencryptSummary:
    workspace_id: UUID | None
    current_key_id: str
    model_provider_credentials: SecretReencryptResourceSummary
    mcp_credential_references: SecretReencryptResourceSummary
    webhook_subscriptions: SecretReencryptResourceSummary

    @property
    def scanned(self) -> int:
        return sum(item.scanned for item in self._resources())

    @property
    def reencrypted(self) -> int:
        return sum(item.reencrypted for item in self._resources())

    @property
    def skipped_current(self) -> int:
        return sum(item.skipped_current for item in self._resources())

    @property
    def failed(self) -> int:
        return sum(item.failed for item in self._resources())

    def to_event_metadata(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id) if self.workspace_id is not None else None,
            "scope": "workspace" if self.workspace_id is not None else "global",
            "current_key_id": self.current_key_id,
            "scanned": self.scanned,
            "reencrypted": self.reencrypted,
            "skipped_current": self.skipped_current,
            "failed": self.failed,
            "resources": {
                "model_provider_credentials": _resource_metadata(
                    self.model_provider_credentials
                ),
                "mcp_credential_references": _resource_metadata(
                    self.mcp_credential_references
                ),
                "webhook_subscriptions": _resource_metadata(
                    self.webhook_subscriptions
                ),
            },
        }

    def _resources(self) -> tuple[SecretReencryptResourceSummary, ...]:
        return (
            self.model_provider_credentials,
            self.mcp_credential_references,
            self.webhook_subscriptions,
        )


@dataclass
class _MutableResourceSummary:
    scanned: int = 0
    reencrypted: int = 0
    skipped_current: int = 0
    failed: int = 0
    from_key_ids: set[str] = field(default_factory=set)

    def freeze(self) -> SecretReencryptResourceSummary:
        return SecretReencryptResourceSummary(
            scanned=self.scanned,
            reencrypted=self.reencrypted,
            skipped_current=self.skipped_current,
            failed=self.failed,
            from_key_ids=tuple(sorted(self.from_key_ids)),
        )


class HostedSecretReencryptService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
    ) -> None:
        self._session = session
        self._secret_service = secret_service

    def reencrypt(self, *, workspace_id: UUID | None = None) -> SecretReencryptSummary:
        summary = SecretReencryptSummary(
            workspace_id=workspace_id,
            current_key_id=self._secret_service.current_key_id,
            model_provider_credentials=self._reencrypt_model_provider_credentials(
                workspace_id=workspace_id,
            ),
            mcp_credential_references=self._reencrypt_mcp_credential_references(
                workspace_id=workspace_id,
            ),
            webhook_subscriptions=self._reencrypt_webhook_subscriptions(
                workspace_id=workspace_id,
            ),
        )
        self._record_security_event(summary)
        return summary

    def _reencrypt_model_provider_credentials(
        self,
        *,
        workspace_id: UUID | None,
    ) -> SecretReencryptResourceSummary:
        statement = select(ModelProviderCredential)
        if workspace_id is not None:
            statement = statement.where(ModelProviderCredential.workspace_id == workspace_id)
        rows = self._session.scalars(statement).all()
        summary = _MutableResourceSummary()
        for credential in rows:
            summary.scanned += 1
            if not self._needs_reencrypt(credential.encryption_key_id):
                summary.skipped_current += 1
                continue
            summary.from_key_ids.add(_key_id_label(credential.encryption_key_id))
            try:
                payload = self._secret_service.decrypt_payload(
                    credential.encrypted_api_key,
                    key_id=credential.encryption_key_id,
                )
            except ValueError:
                summary.failed += 1
                continue
            encrypted = self._secret_service.encrypt_payload(payload)
            credential.encrypted_api_key = encrypted.ciphertext
            credential.api_key_fingerprint = encrypted.fingerprint
            credential.encryption_key_id = encrypted.key_id
            summary.reencrypted += 1
        return summary.freeze()

    def _reencrypt_mcp_credential_references(
        self,
        *,
        workspace_id: UUID | None,
    ) -> SecretReencryptResourceSummary:
        statement = select(McpCredentialReference).where(
            McpCredentialReference.encrypted_secret_payload.is_not(None),
            or_(
                McpCredentialReference.provider == "hosted",
                McpCredentialReference.external_ref == "",
            ),
        )
        if workspace_id is not None:
            statement = statement.where(McpCredentialReference.workspace_id == workspace_id)
        rows = self._session.scalars(statement).all()
        summary = _MutableResourceSummary()
        for credential in rows:
            summary.scanned += 1
            if not self._needs_reencrypt(credential.encryption_key_id):
                summary.skipped_current += 1
                continue
            summary.from_key_ids.add(_key_id_label(credential.encryption_key_id))
            try:
                payload = self._secret_service.decrypt_payload(
                    credential.encrypted_secret_payload or "",
                    key_id=credential.encryption_key_id,
                )
            except ValueError:
                summary.failed += 1
                continue
            encrypted = self._secret_service.encrypt_payload(payload)
            credential.encrypted_secret_payload = encrypted.ciphertext
            credential.secret_fingerprint = encrypted.fingerprint
            credential.encryption_key_id = encrypted.key_id
            summary.reencrypted += 1
        return summary.freeze()

    def _reencrypt_webhook_subscriptions(
        self,
        *,
        workspace_id: UUID | None,
    ) -> SecretReencryptResourceSummary:
        statement = select(WebhookSubscription)
        if workspace_id is not None:
            statement = statement.where(WebhookSubscription.workspace_id == workspace_id)
        rows = self._session.scalars(statement).all()
        summary = _MutableResourceSummary()
        for subscription in rows:
            summary.scanned += 1
            if not self._needs_reencrypt(subscription.encryption_key_id):
                summary.skipped_current += 1
                continue
            summary.from_key_ids.add(_key_id_label(subscription.encryption_key_id))
            try:
                payload = self._secret_service.decrypt_payload(
                    subscription.encrypted_signing_secret,
                    key_id=subscription.encryption_key_id,
                )
            except ValueError:
                summary.failed += 1
                continue
            encrypted = self._secret_service.encrypt_payload(payload)
            subscription.encrypted_signing_secret = encrypted.ciphertext
            subscription.signing_secret_fingerprint = encrypted.fingerprint
            subscription.encryption_key_id = encrypted.key_id
            summary.reencrypted += 1
        return summary.freeze()

    def _needs_reencrypt(self, key_id: str | None) -> bool:
        return key_id != self._secret_service.current_key_id

    def _record_security_event(self, summary: SecretReencryptSummary) -> None:
        event = SecurityEvent(
            workspace_id=summary.workspace_id,
            user_id=None,
            action="secret.reencrypt",
            outcome="success" if summary.failed == 0 else "partial_failure",
            severity="info" if summary.failed == 0 else "warning",
            source_ip=None,
            user_agent=None,
            request_id=None,
            path="worker.secret_reencrypt",
            method="JOB",
            reason="Hosted encrypted secrets re-encrypted with current key",
            event_metadata=summary.to_event_metadata(),
            created_at=datetime.now(UTC),
        )
        self._session.add(event)


def _resource_metadata(summary: SecretReencryptResourceSummary) -> dict[str, object]:
    return {
        "scanned": summary.scanned,
        "reencrypted": summary.reencrypted,
        "skipped_current": summary.skipped_current,
        "failed": summary.failed,
        "from_key_ids": list(summary.from_key_ids),
    }


def _key_id_label(key_id: str | None) -> str:
    return key_id if key_id else "unknown"
