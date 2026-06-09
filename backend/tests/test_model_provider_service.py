import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.pagination import PageParams
from backend.app.audit.models import AuditEvent
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.model_providers import service as model_provider_service_module
from backend.app.model_providers.health import (
    ModelProviderHealthCheck,
    ModelProviderHealthCheckResult,
)
from backend.app.model_providers.model_api import (
    model_api_for_agent_provider,
    unsupported_agent_model_api,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.service import (
    ModelProviderCredentialService,
    ModelProviderUnavailableError,
)
from backend.app.secrets.service import SecretEncryptionService
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_agent_model_api_override_is_limited_to_provider_supported_protocols() -> None:
    assert model_api_for_agent_provider(
        "openai-compatible",
        {"model_api": "response"},
        {"model_api": "chat-completions"},
    ) == "responses"
    assert unsupported_agent_model_api(
        "openai-compatible",
        {"model_api": "response"},
    ) is None

    assert model_api_for_agent_provider(
        "anthropic",
        {"model_api": "response"},
        {"model_api": "chat-completions"},
    ) == "anthropic_messages"
    assert unsupported_agent_model_api(
        "anthropic",
        {"model_api": "response"},
    ) == "responses"


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
    assert audit.audit_metadata["base_url_configured"] is True
    assert audit.audit_metadata["base_url_host"] == "api.openai.com"
    assert audit.audit_metadata["model_api"] is None
    assert audit.audit_metadata["model_apis"] == ["responses", "chat_completions"]
    assert audit.audit_metadata["default_model_api"] is None
    assert "base_url" not in audit.audit_metadata


def test_create_normalizes_openai_compatible_base_url() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)

    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Gateway",
        provider="openai-compatible",
        api_key="sk-secret",
        default_model="provider/default",
        base_url="https://dash.ovload.com/",
        is_default=False,
    )

    assert credential.base_url == "https://dash.ovload.com/v1"


def test_create_canonicalizes_provider_alias_and_normalizes_matching_base_url() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)

    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Gateway",
        provider=" OpenAI_Compatible ",
        api_key="sk-secret",
        default_model="provider/default",
        base_url="https://dash.ovload.com/",
        is_default=False,
    )

    assert credential.provider == "openai-compatible"
    assert credential.base_url == "https://dash.ovload.com/v1"


def test_create_canonicalizes_human_provider_aliases() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)

    anthropic = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Claude",
        provider="Claude API",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com/",
        is_default=False,
    )
    gateway = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Gateway",
        provider="OpenAI Compatible Gateway",
        api_key="sk-secret",
        default_model="provider/default",
        base_url="https://dash.ovload.com/",
        is_default=False,
    )

    assert anthropic.provider == "anthropic"
    assert anthropic.base_url == "https://api.anthropic.com"
    assert gateway.provider == "openai-compatible"
    assert gateway.base_url == "https://dash.ovload.com/v1"


def test_update_normalizes_openai_compatible_base_url_and_drops_query() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Gateway",
        provider="openai-compatible",
        api_key="sk-secret",
        default_model="provider/default",
        base_url=None,
        is_default=False,
    )

    updated = service.update(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
        base_url="https://dash.ovload.com/?token=secret",
    )

    assert updated.base_url == "https://dash.ovload.com/v1"


def test_update_can_set_and_clear_model_api() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Gateway",
        provider="openai-compatible",
        api_key="sk-secret",
        default_model="provider/default",
        base_url="https://dash.ovload.com/v1",
        is_default=False,
    )

    updated = service.update(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
        model_api="response",
        model_api_provided=True,
    )
    assert updated.budget_metadata["model_api"] == "responses"

    cleared = service.update(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
        model_api=None,
        model_api_provided=True,
    )

    assert "model_api" not in cleared.budget_metadata


def test_model_provider_rejects_unknown_or_unsupported_model_api() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)

    with pytest.raises(ValueError, match="Unsupported model_api"):
        service.create(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            name="Gateway",
            provider="openai-compatible",
            api_key="sk-secret",
            default_model="provider/default",
            base_url="https://dash.ovload.com/v1",
            is_default=False,
            model_api="streaming-v3",
        )

    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Anthropic",
        provider="anthropic",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com/",
        is_default=False,
    )

    with pytest.raises(ValueError, match="not supported by provider"):
        service.update(
            workspace_id=workspace.id,
            credential_id=credential.id,
            actor_user_id=user.id,
            model_api="response",
            model_api_provided=True,
        )


def test_update_canonicalizes_provider_alias_before_base_url_normalization() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Anthropic",
        provider="Claude",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com/",
        is_default=False,
    )

    updated = service.update(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
        provider=" OpenAI_Compatible ",
        base_url="https://dash.ovload.com/",
    )

    assert updated.provider == "openai-compatible"
    assert updated.base_url == "https://dash.ovload.com/v1"


def test_update_provider_alias_renormalizes_existing_base_url() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Gateway",
        provider="Claude",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://dash.ovload.com/",
        is_default=False,
    )

    updated = service.update(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
        provider="OpenAI_Compatible",
    )

    assert updated.provider == "openai-compatible"
    assert updated.base_url == "https://dash.ovload.com/v1"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1/v1",
        "https://localhost/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/v1",
    ],
)
def test_create_rejects_unsafe_base_url(base_url: str) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)

    with pytest.raises(ValueError):
        service.create(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            name="Unsafe",
            provider="openai-compatible",
            api_key="sk-secret",
            default_model="provider/default",
            base_url=base_url,
            is_default=False,
        )


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


def test_database_enforces_single_active_default_per_workspace() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    active_default = _raw_credential(
        workspace_id=workspace.id,
        user_id=user.id,
        name="Active Default",
        is_default=True,
        status="active",
    )
    disabled_default = _raw_credential(
        workspace_id=workspace.id,
        user_id=user.id,
        name="Disabled Default",
        is_default=True,
        status="disabled",
    )
    session.add_all([active_default, disabled_default])
    session.commit()

    session.add(
        _raw_credential(
            workspace_id=workspace.id,
            user_id=user.id,
            name="Second Active Default",
            is_default=True,
            status="active",
        )
    )

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError("Expected database to reject two active defaults in one workspace")


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


def test_resolve_exposes_configured_model_api() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Gateway",
        provider="openai-compatible",
        api_key="sk-gateway",
        default_model="gateway-model",
        base_url="https://llm.example.test/v1",
        is_default=True,
        model_api="chat-completions",
    )

    resolved = service.resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=None,
        agent_model="workspace-default",
    )

    assert resolved.credential_id == credential.id
    assert resolved.model_api == "chat_completions"
    assert credential.budget_metadata["model_api"] == "chat_completions"


def test_resolve_preserves_non_openai_provider_and_base_url() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Anthropic",
        provider="anthropic",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com/",
        is_default=True,
        budget_metadata={"model_api": "anthropic_messages"},
    )

    resolved = service.resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=None,
        agent_model="workspace-default",
    )

    assert credential.base_url == "https://api.anthropic.com"
    assert resolved.provider == "anthropic"
    assert resolved.base_url == "https://api.anthropic.com"
    assert resolved.model == "claude-sonnet-4-5"
    assert resolved.model_api == "anthropic_messages"


def test_anthropic_health_check_uses_default_messages_model_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Anthropic",
        provider="anthropic",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        is_default=True,
    )
    captured: dict[str, object] = {}

    async def fake_probe(target, *, probes, timeout_seconds):
        captured["provider"] = target.provider
        captured["model_api"] = target.model_api
        captured["probes"] = probes
        captured["timeout_seconds"] = timeout_seconds
        return ModelProviderHealthCheckResult(
            status="healthy",
            checks=(
                ModelProviderHealthCheck(
                    name="inference",
                    status="passed",
                    metadata={"model_api": target.model_api},
                ),
            ),
        )

    monkeypatch.setattr(model_provider_service_module, "probe_model_provider", fake_probe)

    result = asyncio.run(
        service.run_health_check(
            workspace_id=workspace.id,
            credential_id=credential.id,
            actor_user_id=user.id,
            probes=("inference",),
            timeout_seconds=2,
        )
    )

    assert result.status == "healthy"
    assert captured == {
        "provider": "anthropic",
        "model_api": "anthropic_messages",
        "probes": ("inference",),
        "timeout_seconds": 2,
    }


def test_resolve_fails_closed_when_default_provider_is_unhealthy() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary",
        provider="openai",
        api_key="sk-primary",
        default_model="primary-model",
        base_url=None,
        is_default=True,
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Backup",
        provider="openai",
        api_key="sk-backup",
        default_model="backup-model",
        base_url=None,
        is_default=False,
    )

    for _ in range(3):
        service.record_failure(
            workspace_id=workspace.id,
            credential_id=primary.id,
            error_code="RuntimeError",
            error_message="provider unavailable",
        )
    with pytest.raises(ModelProviderUnavailableError):
        service.resolve_for_agent(
            workspace_id=workspace.id,
            agent_credential_id=None,
            agent_model="workspace-default",
        )

    assert primary.failure_count == 3
    assert primary.health_status == "unhealthy"
    assert backup.status == "active"


def test_resolve_fails_closed_when_default_provider_is_disabled() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary",
        provider="openai",
        api_key="sk-primary",
        default_model="primary-model",
        base_url=None,
        is_default=True,
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Backup",
        provider="openai",
        api_key="sk-backup",
        default_model="backup-model",
        base_url=None,
        is_default=False,
    )

    service.disable(
        workspace_id=workspace.id,
        credential_id=primary.id,
        actor_user_id=user.id,
    )
    with pytest.raises(ModelProviderUnavailableError):
        service.resolve_for_agent(
            workspace_id=workspace.id,
            agent_credential_id=None,
            agent_model="workspace-default",
        )

    assert primary.status == "disabled"
    assert primary.is_default is False
    assert backup.status == "active"


def test_resolve_fails_closed_when_default_budget_is_exhausted() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary",
        provider="openai",
        api_key="sk-primary",
        default_model="primary-model",
        base_url=None,
        is_default=True,
        budget_metadata={"limits": {"calls": 1}, "usage": {"calls": 1}},
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Backup",
        provider="openai",
        api_key="sk-backup",
        default_model="backup-model",
        base_url=None,
        is_default=False,
    )

    with pytest.raises(ModelProviderUnavailableError):
        service.resolve_for_agent(
            workspace_id=workspace.id,
            agent_credential_id=None,
            agent_model="workspace-default",
        )

    assert primary.budget_metadata == {"limits": {"calls": 1}, "usage": {"calls": 1}}
    assert backup.status == "active"


def test_resolve_treats_future_exhausted_until_as_temporarily_exhausted() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary",
        provider="openai",
        api_key="sk-primary",
        default_model="primary-model",
        base_url=None,
        is_default=True,
        budget_metadata={"exhausted_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
    )
    service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Backup",
        provider="openai",
        api_key="sk-backup",
        default_model="backup-model",
        base_url=None,
        is_default=False,
    )

    with pytest.raises(ModelProviderUnavailableError):
        service.resolve_for_agent(
            workspace_id=workspace.id,
            agent_credential_id=None,
            agent_model="workspace-default",
        )
    primary.budget_metadata = {
        "exhausted_until": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    }
    available = service.resolve_for_agent(
        workspace_id=workspace.id,
        agent_credential_id=None,
        agent_model="workspace-default",
    )

    assert available.credential_id == primary.id


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


def test_resolve_without_default_fails_closed() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)

    with pytest.raises(ValueError, match="No available model provider credential"):
        _service(session).resolve_for_agent(
            workspace_id=workspace.id,
            agent_credential_id=None,
            agent_model="gpt-4.1",
        )


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


def test_update_audit_redacts_full_base_url() -> None:
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
        base_url="https://router.example.test/v1/private-path?token=secret",
        is_default=False,
    )

    service.update(
        workspace_id=workspace.id,
        credential_id=credential.id,
        actor_user_id=user.id,
        default_model="router/new",
        base_url="https://new-router.example.test/v1/private-path?token=secret",
    )

    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "model_provider_credential.updated",
        )
    )

    assert audit is not None
    assert audit.audit_metadata["base_url_configured"] is True
    assert audit.audit_metadata["base_url_host"] == "new-router.example.test"
    assert "base_url" not in audit.audit_metadata
    assert "private-path" not in str(audit.audit_metadata)
    assert "secret" not in str(audit.audit_metadata)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1/v1",
        "https://localhost/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/v1",
    ],
)
def test_update_rejects_unsafe_base_url(base_url: str) -> None:
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
        base_url="https://router.example.test/v1",
        is_default=False,
    )

    with pytest.raises(ValueError):
        service.update(
            workspace_id=workspace.id,
            credential_id=credential.id,
            actor_user_id=user.id,
            base_url=base_url,
        )

    assert credential.base_url == "https://router.example.test/v1"


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
    assert disabled.status == "disabled"
    assert disabled.is_default is False
    with pytest.raises(ValueError, match="No available model provider credential"):
        service.resolve_for_agent(
            workspace_id=workspace.id,
            agent_credential_id=None,
            agent_model="gpt-4.1",
        )


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
    assert credential.failure_count == 1

    service.record_success(workspace_id=workspace.id, credential_id=credential.id)
    assert credential.health_status == "healthy"
    assert credential.failure_count == 0
    assert credential.last_success_at is not None
    assert credential.last_failure_code is None
    assert credential.last_failure_message is None


def test_health_check_persists_result_and_records_redacted_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = _service(session)
    credential = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Claude Gateway",
        provider="openai-compatible",
        api_key="sk-health-check",
        default_model="claude-opus-4-6",
        base_url="https://dash.ovload.com/v1",
        is_default=False,
        budget_metadata={"model_api": "chat-completions"},
    )
    captured: dict[str, object] = {}

    async def fake_probe(target, *, probes, timeout_seconds):
        captured["provider"] = target.provider
        captured["model"] = target.model
        captured["api_key"] = target.api_key
        captured["base_url"] = target.base_url
        captured["model_api"] = target.model_api
        captured["probes"] = probes
        captured["timeout_seconds"] = timeout_seconds
        return ModelProviderHealthCheckResult(
            status="degraded",
            checks=(
                ModelProviderHealthCheck(
                    name="models",
                    status="passed",
                    metadata={"model_count": 4, "model_present": True},
                ),
                ModelProviderHealthCheck(
                    name="inference",
                    status="failed",
                    code="permission_denied",
                    message=(
                        "Your request was blocked api_key=sk-health-check "
                        "Bearer health-token "
                        "base_url=https://dash.ovload.com/v1/private"
                    ),
                    metadata={
                        "status_code": 403,
                        "api_key": "sk-health-check",
                        "authorization": "Bearer health-token",
                        "base_url": "https://dash.ovload.com/v1/private",
                    },
                ),
            ),
        )

    monkeypatch.setattr(model_provider_service_module, "probe_model_provider", fake_probe)

    result = asyncio.run(
        service.run_health_check(
            workspace_id=workspace.id,
            credential_id=credential.id,
            actor_user_id=user.id,
            probes=("models", "inference"),
            timeout_seconds=3,
        )
    )
    session.refresh(credential)

    assert result.status == "degraded"
    assert captured == {
        "provider": "openai-compatible",
        "model": "claude-opus-4-6",
        "api_key": "sk-health-check",
        "base_url": "https://dash.ovload.com/v1",
        "model_api": "chat_completions",
        "probes": ("models", "inference"),
        "timeout_seconds": 3,
    }
    assert credential.health_status == "degraded"
    assert credential.failure_count == 1
    assert credential.budget_metadata["model_api"] == "chat_completions"
    assert credential.last_failure_code == "permission_denied"
    assert credential.last_failure_message == "[redacted]"
    audit = session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "model_provider_credential.health_checked"
        )
    ).one()
    assert audit.audit_metadata["status"] == "degraded"
    assert audit.audit_metadata["model_api"] == "chat_completions"
    assert audit.audit_metadata["model_apis"] == ["responses", "chat_completions"]
    assert audit.audit_metadata["default_model_api"] is None
    assert audit.audit_metadata["base_url_host"] == "dash.ovload.com"
    assert audit.audit_metadata["checks"][1]["code"] == "permission_denied"
    assert audit.audit_metadata["checks"][1]["message"] == "[redacted]"
    assert audit.audit_metadata["checks"][1]["metadata"] == {
        "status_code": 403,
        "api_key": "[redacted]",
        "authorization": "[redacted]",
        "base_url": "[redacted]",
    }
    assert "sk-health-check" not in str(audit.audit_metadata)
    assert "Bearer health-token" not in str(audit.audit_metadata)
    assert "dash.ovload.com/v1/private" not in str(audit.audit_metadata)
    assert "dash.ovload.com/v1" not in str(audit.audit_metadata)


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


def _raw_credential(
    *,
    workspace_id,
    user_id,
    name: str,
    is_default: bool,
    status: str,
) -> ModelProviderCredential:
    return ModelProviderCredential(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name=name,
        provider="openai",
        default_model="gpt-4.1",
        encrypted_api_key=f"encrypted-{name}",
        api_key_fingerprint=f"sha256:{name}",
        encryption_key_id="test-key",
        is_default=is_default,
        status=status,
    )


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
