import json
from collections.abc import Generator
from uuid import UUID

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agent_runtime.sessions import (
    PersistentAgentSession,
    PersistentAgentSessionItem,
)
from backend.app.agents.models import AgentProfile, AgentProfileVersion
from backend.app.audit.models import AuditEvent
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.secrets.service import SecretEncryptionService
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_agent_management_lifecycle_versions_and_sessions() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other_owner, other_workspace = _seed_workspace(
        session,
        email="other@example.com",
        slug="other-space",
    )

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Researcher",
            "role": "researcher",
            "instructions": "Remember the company strategy.",
            "model_api": "response",
            "model_settings": {"temperature": 0.2},
        },
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]
    assert created.json()["version"] == 1
    assert created.json()["model_provider"]["source"] == "agent_model"
    assert created.json()["model_provider"]["selected_model"] == "gpt-4.1"
    assert created.json()["model_provider"]["provider"] is None
    assert created.json()["model_settings"] == {
        "temperature": 0.2,
        "model_api": "responses",
    }
    assert created.json()["model_provider"]["model_api"] == "responses"
    assert created.json()["model_provider"]["model_apis"] == []
    assert created.json()["model_provider"]["default_model_api"] is None
    assert created.json()["model_provider"]["model_capability"] is None
    assert created.json()["model_provider"]["credential_status"] is None
    assert created.json()["model_provider"]["credential_health_status"] is None
    assert created.json()["model_provider"]["budget_exhausted"] is False
    assert created.json()["model_provider"]["readiness_status"] == "blocked"
    assert created.json()["model_provider"]["reasons"] == ["model_provider_unavailable"]

    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}",
        headers=_headers(owner.id),
        json={
            "instructions": "Remember product and market context.",
            "model_api": None,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["model_settings"] == {"temperature": 0.2}
    assert updated.json()["model_provider"]["model_api"] is None

    archived = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/archive",
        headers=_headers(owner.id),
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["version"] == 3

    activated = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/activate",
        headers=_headers(owner.id),
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["version"] == 4

    versions = client.get(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/versions",
        headers=_headers(owner.id),
    )
    assert versions.status_code == 200
    assert versions.json()["total"] == 4
    assert [item["version"] for item in versions.json()["items"]] == [4, 3, 2, 1]
    assert versions.json()["items"][0]["changed_by_user_id"] == str(owner.id)

    rolled_back = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/versions/1/rollback",
        headers=_headers(owner.id),
        json={"reason": "Restore original researcher prompt"},
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["version"] == 5
    assert rolled_back.json()["instructions"] == "Remember the company strategy."

    cloned = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/clone",
        headers=_headers(owner.id),
        json={"name": "Researcher Clone"},
    )
    assert cloned.status_code == 201
    assert cloned.json()["name"] == "Researcher Clone"
    assert cloned.json()["version"] == 1

    foreign_get = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/agents/{agent_id}",
        headers=_headers(other_owner.id),
    )
    assert foreign_get.status_code == 404

    agent_uuid = UUID(agent_id)
    agent = session.get(AgentProfile, agent_uuid)
    assert agent is not None
    persistent_session = PersistentAgentSession(
        workspace_id=workspace.id,
        session_key=f"{workspace.id}:team_agent:team-1:{agent_id}",
        scope_type="team_agent",
        scope_id=f"team-1:{agent_id}",
        agent_profile_id=agent.id,
        session_metadata={"source": "test"},
    )
    session.add(persistent_session)
    session.flush()
    session.add_all(
        [
            PersistentAgentSessionItem(
                workspace_id=workspace.id,
                persistent_session_id=persistent_session.id,
                sequence=1,
                item={"role": "user", "content": "first"},
            ),
            PersistentAgentSessionItem(
                workspace_id=workspace.id,
                persistent_session_id=persistent_session.id,
                sequence=2,
                item={"role": "assistant", "content": "second"},
            ),
        ]
    )
    session.commit()

    sessions = client.get(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/sessions",
        headers=_headers(owner.id),
    )
    assert sessions.status_code == 200
    assert sessions.json()["total"] == 1
    assert sessions.json()["items"][0]["item_count"] == 2

    invalid_status_sessions = client.get(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/sessions?status=bad",
        headers=_headers(owner.id),
    )
    assert invalid_status_sessions.status_code == 400

    frozen = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/sessions/"
        f"{persistent_session.id}/freeze",
        headers=_headers(owner.id),
    )
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"

    cleared = client.delete(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/sessions/"
        f"{persistent_session.id}/items",
        headers=_headers(owner.id),
    )
    assert cleared.status_code == 200
    assert cleared.json()["deleted_item_count"] == 2

    deleted = client.delete(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}",
        headers=_headers(owner.id),
    )
    assert deleted.status_code == 204
    session.expire_all()
    assert session.get(AgentProfile, agent_uuid) is None
    assert (
        session.scalar(
            select(AgentProfileVersion).where(
                AgentProfileVersion.agent_profile_id == agent_uuid,
            )
        )
        is None
    )


def test_agent_management_rejects_unknown_model_api() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
    )
    session.add(agent)
    session.commit()

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Bad Protocol",
            "role": "researcher",
            "model_api": "streaming-v3",
        },
    )
    patched = client.patch(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent.id}",
        headers=_headers(owner.id),
        json={"model_api": "streaming-v3"},
    )

    assert created.status_code == 400
    assert "Unsupported model_api" in created.json()["error"]["message"]
    assert patched.status_code == 400
    assert "Unsupported model_api" in patched.json()["error"]["message"]


def test_agent_management_validates_model_provider_credentials_across_versions() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other_owner, other_workspace = _seed_workspace(
        session,
        email="other-provider@example.com",
        slug="other-provider-space",
    )
    provider_service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    )
    primary = provider_service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Primary Claude Gateway",
        provider="anthropic",
        api_key="sk-agent-primary-provider",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        is_default=False,
    )
    backup = provider_service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Backup OpenAI Gateway",
        provider="openai-compatible",
        api_key="sk-agent-backup-provider",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1",
        is_default=False,
    )
    disabled = provider_service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Disabled Gateway",
        provider="openai-compatible",
        api_key="sk-agent-disabled-provider",
        default_model="gpt-4.1-mini",
        base_url="https://disabled-provider.example.test/v1",
        is_default=False,
    )
    disabled.status = "disabled"
    foreign = provider_service.create(
        workspace_id=other_workspace.id,
        created_by_user_id=other_owner.id,
        name="Foreign Gateway",
        provider="openai-compatible",
        api_key="sk-agent-foreign-provider",
        default_model="foreign-model",
        base_url="https://foreign-provider.example.test/v1",
        is_default=False,
    )
    session.commit()

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Provider Agent",
            "role": "researcher",
            "model": "workspace-default",
            "model_provider_credential_id": str(primary.id),
            "model_api": "response",
            "model_settings": {
                "temperature": 0.2,
                "api_key": "sk-agent-model-settings-secret",
            },
        },
    )
    agent_id = created.json()["id"]
    versions = client.get(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/versions",
        headers=_headers(owner.id),
    )
    patched = client.patch(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}",
        headers=_headers(owner.id),
        json={
            "model_provider_credential_id": str(backup.id),
            "model_api": "response",
        },
    )
    cross_workspace_patch = client.patch(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": str(foreign.id)},
    )
    disabled_patch = client.patch(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": str(disabled.id)},
    )
    disabled_clone = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/clone",
        headers=_headers(owner.id),
        json={
            "name": "Disabled Clone",
            "model_provider_credential_id": str(disabled.id),
        },
    )
    historical_version = session.scalar(
        select(AgentProfileVersion).where(
            AgentProfileVersion.workspace_id == workspace.id,
            AgentProfileVersion.agent_profile_id == UUID(agent_id),
            AgentProfileVersion.version == 1,
        )
    )
    assert historical_version is not None
    historical_snapshot = dict(historical_version.snapshot)
    historical_snapshot["model_provider_credential_id"] = str(disabled.id)
    historical_version.snapshot = historical_snapshot
    session.commit()
    rollback_to_disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/versions/1/rollback",
        headers=_headers(owner.id),
        json={"reason": "Restore disabled model provider"},
    )

    assert created.status_code == 201
    assert created.json()["model_provider_credential_id"] == str(primary.id)
    assert created.json()["model_provider"] == {
        "source": "agent_override",
        "selected_model": "claude-sonnet-4-5",
        "agent_model": "workspace-default",
        "credential_id": str(primary.id),
        "credential_reference": f"model_provider_credentials:{primary.id}",
        "credential_name": "Primary Claude Gateway",
        "provider": "anthropic",
        "default_model": "claude-sonnet-4-5",
        "base_url_host": "api.anthropic.com",
        "base_url_configured": True,
        "api_key_fingerprint": primary.api_key_fingerprint,
        "is_default": False,
        "model_api": "anthropic_messages",
        "requested_model_api": "responses",
        "model_apis": ["anthropic_messages"],
        "default_model_api": "anthropic_messages",
        "model_capability": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "display_name": "Claude Sonnet 4.5",
            "capabilities": ["tools", "vision", "streaming"],
            "supports_tools": True,
            "supports_vision": True,
            "supports_json_mode": False,
            "supports_streaming": True,
            "context_window_tokens": 200000,
            "notes": None,
        },
        "credential_status": "active",
        "credential_health_status": "unknown",
        "failure_count": 0,
        "budget_exhausted": False,
        "last_failure_code": None,
        "readiness_status": "degraded",
        "reasons": [],
        "warnings": ["model_provider_unknown", "model_api_override_unsupported"],
    }
    assert "sk-agent-model-settings-secret" not in json.dumps(created.json())
    assert versions.status_code == 200
    assert versions.json()["items"][0]["snapshot"]["model_provider_credential_id"] == str(
        primary.id
    )
    assert "sk-agent-model-settings-secret" not in json.dumps(versions.json())
    assert patched.status_code == 200
    assert patched.json()["model_provider_credential_id"] == str(backup.id)
    assert patched.json()["model_provider"]["provider"] == "openai-compatible"
    assert patched.json()["model_provider"]["selected_model"] == "gpt-4.1-mini"
    assert patched.json()["model_provider"]["model_apis"] == [
        "responses",
        "chat_completions",
    ]
    assert patched.json()["model_provider"]["model_api"] == "responses"
    assert patched.json()["model_provider"]["default_model_api"] is None
    assert patched.json()["model_provider"]["model_capability"]["model"] == "*"
    assert patched.json()["model_provider"]["model_capability"]["supports_json_mode"] is True
    assert patched.json()["model_provider"]["readiness_status"] == "degraded"
    assert patched.json()["model_provider"]["warnings"] == ["model_provider_unknown"]
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
    )
    fetched = client.get(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}",
        headers=_headers(owner.id),
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["model_provider"]["credential_id"] == str(backup.id)
    assert fetched.status_code == 200
    assert fetched.json()["model_provider"] == patched.json()["model_provider"]
    assert cross_workspace_patch.status_code == 404
    assert disabled_patch.status_code == 400
    assert "not active" in disabled_patch.json()["error"]["message"]
    assert disabled_clone.status_code == 400
    assert "not active" in disabled_clone.json()["error"]["message"]
    assert rollback_to_disabled.status_code == 400
    assert "not active" in rollback_to_disabled.json()["error"]["message"]
    assert "sk-agent-primary-provider" not in str(created.json())
    assert "sk-agent-backup-provider" not in str(patched.json())
    assert "sk-agent-disabled-provider" not in str(disabled_patch.json())
    assert "sk-agent-foreign-provider" not in str(cross_workspace_patch.json())
    audits = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.target_type == "agent_profile",
            AuditEvent.target_id == agent_id,
        )
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
    ).all()
    audit_by_action = {audit.action: audit.audit_metadata for audit in audits}
    assert "agent.created" in audit_by_action
    assert "agent.updated" in audit_by_action
    update_audit = audit_by_action["agent.updated"]
    assert update_audit["changed_fields"] == [
        "model_provider_credential_id",
        "model_settings",
    ]
    assert update_audit["before"]["model_provider_credential_id"] == str(primary.id)
    assert update_audit["after"]["model_provider_credential_id"] == str(backup.id)
    assert update_audit["model_provider"] == {
        "credential_id": str(backup.id),
        "provider": "openai-compatible",
        "default_model": "gpt-4.1-mini",
        "model_api": "responses",
        "model_apis": ["responses", "chat_completions"],
        "default_model_api": None,
        "credential_status": "active",
        "credential_health_status": "unknown",
        "is_default": False,
    }
    assert "sk-agent-primary-provider" not in str(update_audit)
    assert "sk-agent-backup-provider" not in str(update_audit)
    assert "provider.example.test/v1" not in str(update_audit)


def test_agent_management_rejects_null_mutable_fields() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Planner", "role": "planner"},
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]

    null_update = client.patch(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}",
        headers=_headers(owner.id),
        json={"instructions": None},
    )
    assert null_update.status_code == 400
    assert "cannot be null" in null_update.json()["error"]["message"]

    null_clone = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents/{agent_id}/clone",
        headers=_headers(owner.id),
        json={"model": None},
    )
    assert null_clone.status_code == 400
    assert "cannot be null" in null_clone.json()["error"]["message"]


def test_agent_management_patch_rejects_unknown_status_field() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Operator", "role": "operator"},
    )
    assert created.status_code == 201

    patched = client.patch(
        f"/api/v1/workspaces/{workspace.id}/agents/{created.json()['id']}",
        headers=_headers(owner.id),
        json={"status": "archived"},
    )
    assert patched.status_code == 422
    assert patched.json()["error"]["code"] == "validation_error"


def _client() -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        json_serializer=json.dumps,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_session = session_factory()
    redis = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        Settings(
            environment="test",
            internal_api_token=TOKEN,
            redis_url="redis://localhost:6379/0",
            database_url="sqlite+pysqlite:///:memory:",
        )
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis
    worker_queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder(app.state.settings.redis_key_prefix),
        queue_name=app.state.settings.worker_queue_name,
        blocking_timeout_seconds=0,
    )
    app.dependency_overrides[get_worker_queue] = lambda: worker_queue
    return TestClient(app), seed_session


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "owner-space",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-User-ID": str(user_id),
    }


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
