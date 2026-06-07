from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.capabilities.models import McpCredentialReference
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.secrets.rotation import HostedSecretReencryptService
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.models import SecurityEvent
from backend.app.webhooks.models import WebhookSubscription
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workspaces.models import Workspace, WorkspaceMember

OLD_SECRET = "old-credential-secret"
NEW_SECRET = "new-credential-secret"


def test_reencrypts_workspace_hosted_secrets_from_previous_key() -> None:
    session = _session()
    _, workspace = _seed_workspace(session, slug="rotation")
    _, other_workspace = _seed_workspace(
        session,
        email="other@example.com",
        slug="other-rotation",
    )
    old_service = SecretEncryptionService(secret=OLD_SECRET, key_id="old-key")
    new_service = _new_secret_service()
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

    summary = HostedSecretReencryptService(session, new_service).reencrypt(
        workspace_id=workspace.id,
    )
    session.commit()
    session.refresh(model_credential)
    session.refresh(mcp_credential)
    session.refresh(webhook)
    session.refresh(foreign_model_credential)

    assert summary.scanned == 3
    assert summary.reencrypted == 3
    assert summary.failed == 0
    assert model_credential.encryption_key_id == "current-key"
    assert mcp_credential.encryption_key_id == "current-key"
    assert webhook.encryption_key_id == "current-key"
    assert foreign_model_credential.encryption_key_id == "old-key"
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
    assert event.event_metadata["reencrypted"] == 3
    assert "sk-model-secret" not in str(event.event_metadata)
    assert "mcp-secret" not in str(event.event_metadata)
    assert "whsec-secret" not in str(event.event_metadata)

    second_summary = HostedSecretReencryptService(session, new_service).reencrypt(
        workspace_id=workspace.id,
    )
    session.commit()

    assert second_summary.reencrypted == 0
    assert second_summary.skipped_current == 3
    assert second_summary.failed == 0


def test_worker_secret_reencrypt_job_can_run_global_maintenance() -> None:
    session = _session()
    _, workspace = _seed_workspace(session, slug="worker-rotation")
    old_service = SecretEncryptionService(secret=OLD_SECRET, key_id="old-key")
    credential = _model_credential(workspace.id, old_service, api_key="sk-worker-secret")
    session.add(credential)
    session.commit()

    WorkerJobHandler(session, settings=_settings()).handle(
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.SECRET_REENCRYPT,
            resource_id=uuid4(),
            idempotency_key=f"secret.reencrypt:{workspace.id}",
            routing={"scope": "global"},
            max_attempts=1,
        )
    )
    session.refresh(credential)

    assert credential.encryption_key_id == "current-key"
    assert _new_secret_service().decrypt_payload(credential.encrypted_api_key) == {
        "api_key": "sk-worker-secret"
    }
    event = session.scalar(select(SecurityEvent).where(SecurityEvent.action == "secret.reencrypt"))
    assert event is not None
    assert event.workspace_id is None
    assert event.event_metadata["scope"] == "global"
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
) -> McpCredentialReference:
    encrypted = secret_service.encrypt_payload({"token": token})
    return McpCredentialReference(
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
        database_url="sqlite+pysqlite:///:memory:",
        internal_api_token="test-token",
        credential_encryption_secret=NEW_SECRET,
        credential_encryption_key_id="current-key",
        credential_encryption_previous_secrets={"old-key": OLD_SECRET},
    )


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
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


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
