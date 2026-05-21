from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.pagination import PageParams
from backend.app.audit.models import AuditEvent
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.secrets.service import SecretEncryptionService
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_create_encrypts_api_key_and_records_audit() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)

    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="OpenAI",
        provider="openai",
        api_key="sk-secret",
        default_model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
        is_default=True,
    )

    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "model_provider_credential.created",
        )
    )
    assert credential.encrypted_api_key != "sk-secret"
    assert credential.api_key_fingerprint.startswith("sha256:")
    assert credential.encryption_key_id == "test-key"
    assert credential.is_default is True
    assert audit is not None
    assert audit.audit_metadata["name"] == "OpenAI"


def test_setting_new_default_unsets_previous_default_in_same_workspace() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)

    first = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="First",
        provider="openai",
        api_key="sk-first",
        default_model="gpt-4.1",
        base_url=None,
        is_default=True,
    )
    second = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Second",
        provider="openai-compatible",
        api_key="sk-second",
        default_model="provider/default",
        base_url="https://llm.example.test/v1",
        is_default=True,
    )

    session.expire_all()
    stored_first = session.get(ModelProviderCredential, first.id)
    stored_second = session.get(ModelProviderCredential, second.id)
    assert stored_first is not None
    assert stored_second is not None
    assert stored_first.is_default is False
    assert stored_second.is_default is True


def test_resolve_uses_workspace_default_when_agent_has_no_override() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Default",
        provider="openai",
        api_key="sk-default",
        default_model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
        is_default=True,
    )

    resolved = service.resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=None,
        agent_model="workspace-default",
    )

    assert resolved.credential_id == credential.id
    assert resolved.api_key == "sk-default"
    assert resolved.base_url == "https://api.openai.com/v1"
    assert resolved.model == "gpt-4.1-mini"


def test_resolve_agent_override_can_use_specific_model() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Router",
        provider="openai-compatible",
        api_key="sk-router",
        default_model="router/default",
        base_url="https://openrouter.ai/api/v1",
        is_default=False,
    )

    resolved = service.resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=credential.id,
        agent_model="anthropic/claude-sonnet",
    )

    assert resolved.credential_id == credential.id
    assert resolved.api_key == "sk-router"
    assert resolved.base_url == "https://openrouter.ai/api/v1"
    assert resolved.model == "anthropic/claude-sonnet"


def test_resolve_rejects_cross_workspace_credential() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    credential = _service(session).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Private",
        provider="openai",
        api_key="sk-private",
        default_model="gpt-4.1",
        base_url=None,
        is_default=False,
    )

    try:
        _service(session).resolve_for_agent(
            workspace_id=other_workspace.id,
            agent_credential_id=credential.id,
            agent_model="gpt-4.1",
        )
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("Expected cross-workspace credential to be rejected")


def test_resolve_without_default_falls_back_to_agent_model() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)

    resolved = _service(session).resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=None,
        agent_model="gpt-4.1",
    )

    assert resolved.credential_id is None
    assert resolved.api_key is None
    assert resolved.base_url is None
    assert resolved.model == "gpt-4.1"


def test_rotate_key_updates_secret_material_without_changing_metadata() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Rotating",
        provider="openai",
        api_key="sk-old",
        default_model="gpt-4.1",
        base_url=None,
        is_default=True,
    )
    old_fingerprint = credential.api_key_fingerprint

    rotated = service.rotate_key(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
        api_key="sk-new",
    )
    resolved = service.resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=credential.id,
        agent_model="workspace-default",
    )

    assert rotated.api_key_fingerprint != old_fingerprint
    assert resolved.api_key == "sk-new"
    assert resolved.model == "gpt-4.1"


def test_disable_removes_credential_from_default_resolution() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Default",
        provider="openai",
        api_key="sk-default",
        default_model="gpt-4.1-mini",
        base_url=None,
        is_default=True,
    )

    disabled = service.disable(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
    )
    resolved = service.resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=None,
        agent_model="gpt-4.1",
    )

    assert disabled.status == "disabled"
    assert disabled.is_default is False
    assert resolved.credential_id is None
    assert resolved.model == "gpt-4.1"


def test_resolve_rejects_disabled_agent_override_credential() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Disabled",
        provider="openai",
        api_key="sk-disabled",
        default_model="gpt-4.1",
        base_url=None,
        is_default=False,
    )
    service.disable(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
    )

    try:
        service.resolve_for_agent(
            workspace_id=workspace.id,
            agent_credential_id=credential.id,
            agent_model="workspace-default",
        )
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("Expected disabled explicit credential override to fail")


def test_records_provider_health_success_and_failure() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Health",
        provider="openai",
        api_key="sk-health",
        default_model="gpt-4.1",
        base_url=None,
        is_default=False,
    )

    service.record_failure(
        workspace_id=workspace.id,
        credential_id=credential.id,
        error_code="RateLimitError",
        error_message="rate limited",
    )
    assert credential.health_status == "degraded"
    assert credential.last_failure_at is not None
    assert credential.last_failure_code == "RateLimitError"
    assert credential.last_failure_message == "rate limited"

    service.record_success(workspace_id=workspace.id, credential_id=credential.id)
    assert credential.health_status == "healthy"
    assert credential.last_success_at is not None
    assert credential.last_failure_code is None
    assert credential.last_failure_message is None


def test_usage_audit_lists_only_sanitized_provider_events_for_workspace() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    session.add_all(
        [
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(user.id),
                user_id=user.id,
                action="model_provider.used",
                target_type="agent_run",
                target_id="run-1",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "task_id": "task-1",
                    "task_step_id": "step-1",
                    "agent_profile_id": "agent-1",
                    "model": "gpt-4.1-mini",
                    "credential_id": "credential-1",
                    "fallback_selected": True,
                    "api_key": "sk-secret",
                    "base_url": "https://secret.example.test/v1",
                },
            ),
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(user.id),
                user_id=user.id,
                action="model_provider.fallback_unavailable",
                target_type="agent_run",
                target_id="run-2",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "reason": {"code": "RuntimeError", "message": "primary failed"},
                    "failed_provider": {
                        "model": "primary",
                        "credential_id": "credential-1",
                        "api_key": "sk-secret",
                        "base_url": "https://secret.example.test/v1",
                    },
                },
            ),
            AuditEvent(
                workspace_id=other_workspace.id,
                actor_type="user",
                actor_id=str(user.id),
                user_id=user.id,
                action="model_provider.used",
                target_type="agent_run",
                target_id="foreign-run",
                created_at=datetime.now(UTC),
                audit_metadata={"model": "foreign"},
            ),
        ]
    )
    session.commit()

    rows, total = _service(session).list_usage_audit(workspace.id, PageParams())
    fallback_rows, fallback_total = _service(session).list_usage_audit(
        workspace.id,
        PageParams(),
        action="model_provider.fallback_unavailable",
    )

    assert total == 2
    assert {row.target_id for row in rows} == {"run-1", "run-2"}
    assert fallback_total == 1
    assert fallback_rows[0].target_id == "run-2"


def _service(session: Session) -> ModelProviderCredentialService:
    return ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
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
    slug: str = "owner",
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
