from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.capabilities.mcp.models import MCPCredentialReference
from opsmesh.governance.credentials.activities import CredentialMaintenanceActivities
from opsmesh.governance.credentials.service import SecretReencryptionService
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.users.models import User
from opsmesh.orchestration.webhooks.models import WebhookSubscription
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.channels import seed_channel
from tests.fixtures.database import product_engine

OLD_SECRET = "old-credential-secret"
NEW_SECRET = "new-credential-secret"


def test_reencrypts_workspace_hosted_secrets_from_previous_key() -> None:
    session = _session()
    owner, workspace = _seed_workspace(session, slug="rotation")
    foreign_owner, other_workspace = _seed_workspace(
        session,
        email="other@example.com",
        slug="other-rotation",
    )
    old_service = SecretEncryptionService(secret=OLD_SECRET, key_id="old-key")
    new_service = _new_secret_service()
    channel, _, _ = seed_channel(session, owner, workspace, secret_service=old_service)
    foreign_channel, _, _ = seed_channel(
        session, foreign_owner, other_workspace, secret_service=old_service
    )
    channel_revision = channel.configuration_revision
    model_credential = _model_credential(workspace.id, old_service, api_key="sk-model-secret")
    mcp_credential = _mcp_credential(workspace.id, old_service, token="mcp-secret")
    webhook = _webhook_subscription(workspace.id, old_service, signing_secret="whsec-secret")
    foreign_model_credential = _model_credential(
        other_workspace.id,
        old_service,
        api_key="sk-foreign-secret",
    )
    session.add_all([model_credential, mcp_credential, webhook, foreign_model_credential])
    session.commit()
    old_ciphertexts = {
        "model": model_credential.encrypted_api_key,
        "mcp": mcp_credential.encrypted_secret_payload,
        "webhook": webhook.encrypted_signing_secret,
        "foreign": foreign_model_credential.encrypted_api_key,
    }

    summary = SecretReencryptionService(session, new_service).reencrypt(
        workspace_id=workspace.id,
    )
    session.commit()
    session.refresh(model_credential)
    session.refresh(mcp_credential)
    session.refresh(webhook)
    session.refresh(foreign_model_credential)

    assert summary.scanned == 4
    assert summary.reencrypted == 4
    assert summary.failed == 0
    assert model_credential.encryption_key_id == "current-key"
    assert mcp_credential.encryption_key_id == "current-key"
    assert webhook.encryption_key_id == "current-key"
    assert foreign_model_credential.encryption_key_id == "old-key"
    assert channel.encryption_key_id == "current-key"
    assert channel.configuration_revision == channel_revision
    assert foreign_channel.encryption_key_id == "old-key"
    assert new_service.decrypt_payload(channel.encrypted_signing_secret)
    assert model_credential.encrypted_api_key != old_ciphertexts["model"]
    assert mcp_credential.encrypted_secret_payload != old_ciphertexts["mcp"]
    assert webhook.encrypted_signing_secret != old_ciphertexts["webhook"]
    assert foreign_model_credential.encrypted_api_key == old_ciphertexts["foreign"]
    assert new_service.decrypt_payload(model_credential.encrypted_api_key) == {
        "api_key": "sk-model-secret"
    }
    assert new_service.decrypt_payload(mcp_credential.encrypted_secret_payload or "") == {
        "token": "mcp-secret"
    }
    assert new_service.decrypt_payload(webhook.encrypted_signing_secret) == {
        "signing_secret": "whsec-secret"
    }

    event = session.scalar(select(SecurityEvent).where(SecurityEvent.action == "secret.reencrypt"))
    assert event is not None
    assert event.workspace_id == workspace.id
    assert event.outcome == "success"
    assert event.event_metadata["reencrypted"] == 4
    assert "sk-model-secret" not in str(event.event_metadata)
    assert "mcp-secret" not in str(event.event_metadata)
    assert "whsec-secret" not in str(event.event_metadata)

    second_summary = SecretReencryptionService(session, new_service).reencrypt(
        workspace_id=workspace.id,
    )
    session.commit()

    assert second_summary.reencrypted == 0
    assert second_summary.skipped_current == 4
    assert second_summary.failed == 0


def test_secret_rotation_activity_is_workspace_scoped_and_rejects_global_escalation() -> None:
    session = _session()
    owner, workspace = _seed_workspace(session, slug="worker-rotation")
    _, other_workspace = _seed_workspace(
        session, slug="foreign-rotation", email="foreign@test.invalid"
    )
    old_service = SecretEncryptionService(secret=OLD_SECRET, key_id="old-key")
    credential = _model_credential(workspace.id, old_service, api_key="sk-worker-secret")
    foreign = _model_credential(other_workspace.id, old_service, api_key="sk-foreign-secret")
    session.add_all([credential, foreign])
    session.commit()

    request = ExecutionRequest(
        workspace_id=workspace.id,
        kind=ExecutionKind.SECRET_REENCRYPT,
        resource_id=workspace.id,
        requested_by_user_id=owner.id,
    )
    services = ActivityDependencies(
        sessions=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        settings=_settings(),
    )
    activity = CredentialMaintenanceActivities(services)
    with pytest.raises(ValueError, match="global"):
        activity.reencrypt(request.model_copy(update={"parameters": {"scope": "global"}}))
    session.refresh(credential)
    assert credential.encryption_key_id == "old-key"
    activity.reencrypt(request)
    session.refresh(credential)

    assert credential.encryption_key_id == "current-key"
    assert _new_secret_service().decrypt_payload(credential.encrypted_api_key) == {
        "api_key": "sk-worker-secret"
    }
    event = session.scalar(select(SecurityEvent).where(SecurityEvent.action == "secret.reencrypt"))
    assert event is not None
    assert event.workspace_id == workspace.id
    session.refresh(foreign)
    assert foreign.encryption_key_id == "old-key"
    assert "sk-worker-secret" not in str(event.event_metadata)


def _model_credential(
    workspace_id: object,
    secret_service: SecretEncryptionService,
    *,
    api_key: str,
) -> ModelProviderCredential:
    encrypted = secret_service.encrypt_payload({"api_key": api_key})
    return ModelProviderCredential(
        workspace_id=workspace_id,
        created_by_user_id=None,
        name=f"Provider {api_key[-6:]}",
        provider="openai",
        default_model="gpt-4.1",
        encrypted_api_key=encrypted.ciphertext,
        api_key_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
        is_default=False,
        status="active",
    )


def _mcp_credential(
    workspace_id: object,
    secret_service: SecretEncryptionService,
    *,
    token: str,
) -> MCPCredentialReference:
    encrypted = secret_service.encrypt_payload({"token": token})
    return MCPCredentialReference(
        workspace_id=workspace_id,
        mcp_server_id=None,
        name=f"MCP {token[-6:]}",
        provider="hosted",
        external_ref="",
        encrypted_secret_payload=encrypted.ciphertext,
        secret_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
        scopes=["read"],
        status="active",
    )


def _webhook_subscription(
    workspace_id: object,
    secret_service: SecretEncryptionService,
    *,
    signing_secret: str,
) -> WebhookSubscription:
    encrypted = secret_service.encrypt_payload({"signing_secret": signing_secret})
    return WebhookSubscription(
        workspace_id=workspace_id,
        created_by_user_id=None,
        name=f"Webhook {signing_secret[-6:]}",
        target_url="https://hooks.example.test/events",
        event_types=["*"],
        encrypted_signing_secret=encrypted.ciphertext,
        signing_secret_fingerprint=encrypted.fingerprint,
        encryption_key_id=encrypted.key_id,
        status="active",
    )


def _new_secret_service() -> SecretEncryptionService:
    return SecretEncryptionService(
        secret=NEW_SECRET,
        key_id="current-key",
        previous_secrets={"old-key": OLD_SECRET},
    )


def _settings() -> Settings:
    return Settings(
        environment="test",
        log_format="text",
        internal_api_token="test-token",
        credential_encryption_secret=NEW_SECRET,
        credential_encryption_key_id="current-key",
        credential_encryption_previous_secrets={"old-key": OLD_SECRET},
    )


def _session() -> Session:
    engine = product_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str,
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace
