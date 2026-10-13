"""Workspace-scoped product API verification on PostgreSQL."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

import opsmesh.agents.providers.routes as model_provider_health_service_module
from opsmesh.agents.messages.models import AgentMessage, AgentMessageThread
from opsmesh.agents.providers.probes import ModelProviderHealthCheck, ModelProviderHealthCheckResult
from opsmesh.agents.sessions.models import (
    AgentSessionMetadata,
    SDKSessionMessageRecord,
    SDKSessionRecord,
)
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.teams.observation.timeline import TeamTimelineService, TimelineFilters
from tests.fixtures.workspace_api import _client, _headers, _seed_workspace


@pytest.mark.parametrize("operation", ["create", "update"])
def test_model_provider_api_rejects_unsupported_provider(operation: str) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    url = f"/api/v1/workspaces/{workspace.id}/model-provider-credentials"
    payload = {
        "name": "Model provider",
        "provider": "openai",
        "api_key": "sk-secret",
        "default_model": "gpt-4.1-mini",
    }
    if operation == "create":
        response = client.post(
            url, headers=_headers(owner.id), json={**payload, "provider": "unsupported-provider"}
        )
    else:
        created = client.post(url, headers=_headers(owner.id), json=payload)
        assert created.status_code == 201
        response = client.patch(
            f"{url}/{created.json()['id']}",
            headers=_headers(owner.id),
            json={"provider": "unsupported-provider"},
        )
        listed = client.get(url, headers=_headers(owner.id))
        assert listed.json()["items"][0]["provider"] == "openai"
    assert response.status_code == 400
    assert "Unsupported model provider" in response.text
    assert "sk-secret" not in response.text


def test_model_provider_credentials_are_created_without_returning_secret() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenAI Prod",
            "provider": "openai",
            "api_key": "sk-secret",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4.1-mini",
            "model_api": "chat_completions",
            "is_default": True,
            "budget_metadata": {
                "limits": {"calls": 10},
                "usage": {"calls": 0},
                "api_key": "sk-budget-secret",
                "nested": {"token": "hidden", "label": "monthly"},
            },
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials", headers=_headers(owner.id)
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "OpenAI Prod"
    assert body["is_default"] is True
    assert body["default_model"] == "gpt-4.1-mini"
    assert body["api_key_fingerprint"].startswith("sha256:")
    assert body["secret_metadata"] == {
        "storage": "hosted_encrypted",
        "provider": "hosted",
        "configured": True,
        "reference_kind": None,
        "reference_version": None,
        "encryption_key_id": "local",
        "fingerprint_configured": True,
        "rotation_state": "current",
        "raw_secret_exposed": False,
    }
    assert body["health_status"] == "unknown"
    assert body["failure_count"] == 0
    assert body["budget_metadata"] == {
        "limits": {"calls": 10},
        "usage": {"calls": 0},
        "model_api": "chat_completions",
        "nested": {"label": "monthly"},
    }
    assert body["model_api"] == "chat_completions"
    assert body["model_capability"] is None
    assert body["last_success_at"] is None
    assert body["last_failure_at"] is None
    assert body["last_health_check_at"] is None
    assert body["last_failure_code"] is None
    assert body["last_failure_message"] is None
    assert body["scheduled_health_check"] == {
        "configured": False,
        "active_count": 0,
        "paused_count": 0,
        "next_run_at": None,
        "jobs": [],
    }
    assert "api_key" not in body
    assert "encrypted_api_key" not in body
    assert "sk-budget-secret" not in json.dumps(body)
    assert "hidden" not in json.dumps(body)
    assert body["base_url_configured"] is True
    assert body["base_url_host"] == "api.openai.com"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["base_url_configured"] is True
    assert listed.json()["items"][0]["base_url_host"] == "api.openai.com"
    assert listed.json()["items"][0]["model_api"] == "chat_completions"
    assert listed.json()["items"][0]["model_capability"] == body["model_capability"]
    assert listed.json()["items"][0]["last_health_check_at"] is None
    assert listed.json()["items"][0]["scheduled_health_check"]["configured"] is False
    assert listed.json()["total"] == 1


def test_model_provider_health_check_updates_status_without_returning_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    async def fake_probe(target, *, probes, timeout_seconds):
        assert target.provider == "openai-compatible"
        assert target.model == "gpt-4.1"
        assert target.model_api == "chat_completions"
        assert target.api_key == "sk-health-api-secret"
        assert target.base_url == "https://dash.ovload.com/v1"
        assert probes == ("models", "inference")
        assert timeout_seconds == 5
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
                    message="Your request was blocked.",
                    metadata={"status_code": 403},
                ),
            ),
        )

    monkeypatch.setattr(model_provider_health_service_module, "probe_model_provider", fake_probe)
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenAI Gateway",
            "provider": "openai-compatible",
            "api_key": "sk-health-api-secret",
            "base_url": "https://dash.ovload.com/v1",
            "default_model": "gpt-4.1",
            "model_api": "chat_completions",
        },
    )
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{credential.json()['id']}/health-check",
        headers=_headers(owner.id),
        json={"probes": ["models", "inference"], "timeout_seconds": 5},
    )
    invalid = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{credential.json()['id']}/health-check",
        headers=_headers(owner.id),
        json={"probes": ["billing"]},
    )
    assert credential.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["credential"]["health_status"] == "degraded"
    assert body["credential"]["model_api"] == "chat_completions"
    assert body["credential"]["failure_count"] == 1
    assert body["credential"]["last_health_check_at"] == body["credential"]["last_failure_at"]
    assert body["credential"]["last_failure_code"] == "permission_denied"
    assert body["credential"]["last_failure_message"] == "Your request was blocked."
    assert body["credential"]["scheduled_health_check"]["configured"] is False
    assert body["checks"][0]["status"] == "passed"
    assert body["checks"][1]["code"] == "permission_denied"
    assert invalid.status_code == 400
    assert "sk-health-api-secret" not in json.dumps(body)
    assert "dash.ovload.com/v1" not in json.dumps(body)


def test_model_provider_capabilities_are_listed_without_secrets() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    for provider, model, model_api in (
        ("openai-compatible", "company-model", "responses"),
        ("openai", "company-openai", "responses"),
    ):
        declared = client.post(
            f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
            headers=_headers(owner.id),
            json={
                "name": provider,
                "provider": provider,
                "api_key": "sk-catalog-fixture",
                "base_url": "https://models.example.test/v1",
                "default_model": model,
                "model_api": model_api,
                "model_capabilities": [
                    {
                        "provider": provider,
                        "model": model,
                        "display_name": model,
                        "supports_vision": False,
                        "supports_tools": True,
                        "supports_json_mode": True,
                        "supports_streaming": True,
                    }
                ],
            },
        )
        assert declared.status_code == 201
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-capabilities", headers=_headers(owner.id)
    )
    openai_tools = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-capabilities",
        headers=_headers(owner.id),
        params={"provider": "openai", "capability": "tools"},
    )
    runtimes = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-capabilities/agent-runtimes",
        headers=_headers(owner.id),
    )
    assert listed.status_code == 200
    assert openai_tools.status_code == 200
    assert runtimes.status_code == 200
    compatible = next(item for item in listed.json() if item["provider"] == "openai-compatible")
    assert compatible["model_apis"] == ["responses", "chat_completions"]
    assert compatible["default_model_api"] is None
    assert openai_tools.json()
    assert {item["provider"] for item in openai_tools.json()} == {"openai"}
    assert all("tools" in item["capabilities"] for item in openai_tools.json())
    assert all(
        item["model_apis"] == ["responses", "chat_completions"]
        and item["default_model_api"] is None
        for item in openai_tools.json()
    )
    runtime_by_provider = {item["provider"]: item for item in runtimes.json()}
    assert set(runtime_by_provider) == {"openai-compatible"}
    openai_features = {
        feature["name"]: feature for feature in runtime_by_provider["openai-compatible"]["features"]
    }
    assert openai_features["tools"]["supported"] is True
    assert openai_features["cancellation"]["supported"] is True
    assert openai_features["handoffs"]["supported"] is True
    assert openai_features["handoffs"]["reason"] is None
    serialized = json.dumps(listed.json()) + json.dumps(runtimes.json())
    assert "api_key" not in serialized
    assert "base_url" not in serialized


def test_create_agent_can_reference_workspace_model_provider_credential() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenRouter",
            "provider": "openai-compatible",
            "api_key": "sk-router",
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "openai/gpt-4.1-mini",
        },
    )
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other@example.com", slug="other"
    )
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Router Agent",
            "role": "researcher",
            "model": "workspace-default",
            "model_provider_credential_id": credential.json()["id"],
        },
    )
    cross_workspace = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/agents",
        headers=_headers(other_owner.id),
        json={
            "name": "Bad Agent",
            "role": "researcher",
            "model_provider_credential_id": credential.json()["id"],
        },
    )
    assert created.status_code == 201
    assert created.json()["model_provider_credential_id"] == credential.json()["id"]
    assert cross_workspace.status_code == 400


def test_team_member_model_provider_can_be_bound_from_operations_context() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-provider-bind@example.com", slug="other-provider-bind"
    )
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenAI Gateway",
            "provider": "openai-compatible",
            "api_key": "sk-router",
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "openai/gpt-4.1-mini",
        },
    )
    disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Disabled",
            "provider": "openai",
            "api_key": "sk-disabled",
            "default_model": "gpt-4.1",
        },
    )
    foreign = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/model-provider-credentials",
        headers=_headers(other_owner.id),
        json={
            "name": "Foreign",
            "provider": "openai",
            "api_key": "sk-foreign",
            "default_model": "gpt-4.1",
        },
    )
    disabled_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{disabled.json()['id']}/disable",
        headers=_headers(owner.id),
    )
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Team Dev", "role": "developer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Provider Ops", "team_type": "company"},
    )
    member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={"agent_profile_id": agent.json()["id"], "team_role": "developer"},
    )
    persistent_session = AgentSessionMetadata(
        workspace_id=workspace.id,
        session_key=f"{workspace.id}:team_agent:{team.json()['id']}:{agent.json()['id']}",
        scope_type="team_agent",
        scope_id=f"{team.json()['id']}:{agent.json()['id']}",
        agent_profile_id=UUID(agent.json()["id"]),
        agent_team_id=UUID(team.json()["id"]),
        session_metadata={"source": "test"},
    )
    session.add(persistent_session)
    session.flush()
    session.add(SDKSessionRecord(session_id=persistent_session.session_key))
    session.flush()
    session.add(
        SDKSessionMessageRecord(
            session_id=persistent_session.session_key,
            message_data=json.dumps({"role": "user", "content": "old provider context"}),
        )
    )
    session.commit()
    bound = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={
            "model_provider_credential_id": credential.json()["id"],
            "model": "workspace-default",
        },
    )
    disabled_bind = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": disabled.json()["id"]},
    )
    foreign_bind = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": foreign.json()["id"]},
    )
    missing_member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{uuid4()}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": credential.json()["id"]},
    )
    empty_update = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={},
    )
    model_only = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model": "openai/gpt-4.1", "reset_session": False},
    )
    model_api_only = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_api": "responses"},
    )
    clear_credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={"model_provider_credential_id": None, "reset_session": False},
    )
    assert credential.status_code == 201
    assert disabled_response.status_code == 200
    assert bound.status_code == 200
    assert bound.json()["model_provider_credential_id"] == credential.json()["id"]
    assert bound.json()["model"] == "workspace-default"
    assert bound.json()["model_provider"]["source"] == "agent_override"
    assert bound.json()["model_provider"]["credential_name"] == "OpenAI Gateway"
    assert bound.json()["model_provider"]["session_reset"] == {
        "reset_session_count": 1,
        "session_ids": [str(persistent_session.id)],
        "reason": "team_member_model_provider_updated",
    }
    assert disabled_bind.status_code == 400
    assert foreign_bind.status_code == 404
    assert missing_member.status_code == 404
    assert empty_update.status_code == 400
    assert "model_api" in str(empty_update.json())
    assert model_only.status_code == 200
    assert model_only.json()["model"] == "openai/gpt-4.1"
    assert model_only.json()["model_provider_credential_id"] == credential.json()["id"]
    assert model_api_only.status_code == 200
    assert model_api_only.json()["model_settings"]["model_api"] == "responses"
    assert model_api_only.json()["model_provider"]["model_api"] == "responses"
    assert model_api_only.json()["model_provider"]["session_reset"] == {
        "reset_session_count": 1,
        "session_ids": [str(persistent_session.id)],
        "reason": "team_member_model_provider_updated",
    }
    assert clear_credential.status_code == 200
    assert clear_credential.json()["model_provider_credential_id"] is None
    assert clear_credential.json()["model_settings"]["model_api"] == "responses"
    session.refresh(persistent_session)
    assert persistent_session.status == "active"
    assert (
        persistent_session.session_metadata["last_reset"]["reason"]
        == "team_member_model_provider_updated"
    )
    assert (
        persistent_session.session_metadata["last_reset"]["metadata"]["before"]["model"]
        == "openai/gpt-4.1"
    )
    assert (
        persistent_session.session_metadata["last_reset"]["metadata"]["after"]["model"]
        == "openai/gpt-4.1"
    )
    assert (
        persistent_session.session_metadata["last_reset"]["metadata"]["before"]["model_api"] is None
    )
    assert (
        persistent_session.session_metadata["last_reset"]["metadata"]["after"]["model_api"]
        == "responses"
    )
    assert (
        session.scalar(
            select(func.count(SDKSessionMessageRecord.id)).where(
                SDKSessionMessageRecord.session_id == persistent_session.session_key
            )
        )
        == 0
    )
    provider_message = session.scalar(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == UUID(team.json()["id"]),
            AgentMessage.message_type == "team.runtime.model_provider.updated",
        )
    )
    assert provider_message is not None
    assert provider_message.thread_id is not None
    assert provider_message.payload["team_member_id"] == member.json()["id"]
    assert provider_message.payload["agent_profile_id"] == agent.json()["id"]
    assert provider_message.payload["reset_session_count"] == 1
    provider_messages = session.scalars(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == UUID(team.json()["id"]),
            AgentMessage.message_type == "team.runtime.model_provider.updated",
        )
    ).all()
    model_api_message = next(
        item for item in provider_messages if item.payload["after"]["model_api"] == "responses"
    )
    assert model_api_message.payload["before"]["model_api"] is None
    assert model_api_message.payload["after"]["model_api"] == "responses"
    assert model_api_message.payload["reset_session_count"] == 1
    timeline = TeamTimelineService(session).timeline(
        workspace_id=workspace.id,
        team_id=UUID(team.json()["id"]),
        filters=TimelineFilters(event_type="team.runtime.model_provider.updated"),
    )
    assert timeline is not None
    assert timeline.summary.event_type_counts["team.runtime.model_provider.updated"] == 4
    assert timeline.items[0].event_type == "team.runtime.model_provider.updated"
    assert timeline.items[0].metadata["reset_session_count"] == 0
    audit_timeline = TeamTimelineService(session).timeline(
        workspace_id=workspace.id,
        team_id=UUID(team.json()["id"]),
        filters=TimelineFilters(
            source_type="audit_event", event_type="team.member_model_provider.updated"
        ),
    )
    assert audit_timeline is not None
    assert audit_timeline.summary.event_type_counts["team.member_model_provider.updated"] == 4
    reset_audit_timeline_item = next(
        item
        for item in audit_timeline.items
        if item.metadata["audit_metadata"]["after"]["model_api"] == "responses"
    )
    assert reset_audit_timeline_item.resource_id is not None
    assert reset_audit_timeline_item.metadata["target_type"] == "agent_team_member"
    assert reset_audit_timeline_item.metadata["target_id"] == member.json()["id"]
    assert "sk-router" not in str(audit_timeline)
    assert "openrouter.ai/api/v1" not in str(audit_timeline)
    audits = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at.desc())
    ).all()
    audit = next(
        (
            item
            for item in audits
            if item.action == "agent.updated"
            and item.audit_metadata.get("changed_fields")
            == ["model", "model_provider_credential_id"]
        ),
        None,
    )
    assert audit is not None
    assert audit.action == "agent.updated"
    assert audit.audit_metadata["changed_fields"] == ["model", "model_provider_credential_id"]
    team_audit = next(
        (
            item
            for item in audits
            if item.action == "team.member_model_provider.updated"
            and item.audit_metadata.get("reset_session_count") == 1
            and (item.audit_metadata["before"]["model"] == "workspace-default")
        ),
        None,
    )
    assert team_audit is not None
    assert team_audit.target_type == "agent_team_member"
    assert team_audit.target_id == str(member.json()["id"])
    assert team_audit.audit_metadata["team_id"] == team.json()["id"]
    assert team_audit.audit_metadata["agent_profile_id"] == agent.json()["id"]
    assert team_audit.audit_metadata["before"]["model"] == "workspace-default"
    assert team_audit.audit_metadata["after"]["model"] == "workspace-default"
    assert team_audit.audit_metadata["before"]["model_api"] is None
    assert team_audit.audit_metadata["after"]["model_api"] is None
    model_api_team_audit = next(
        (
            item
            for item in audits
            if item.action == "team.member_model_provider.updated"
            and item.audit_metadata["after"]["model_api"] == "responses"
            and (item.audit_metadata["reset_session_count"] == 1)
        ),
        None,
    )
    assert model_api_team_audit is not None
    assert model_api_team_audit.audit_metadata["changed"] is True
    assert model_api_team_audit.audit_metadata["reset_session_count"] == 1
    assert model_api_team_audit.audit_metadata["before"]["model_api"] is None
    assert model_api_team_audit.audit_metadata["after"]["model_api"] == "responses"
    assert "sk-router" not in str(team_audit.audit_metadata)
    assert "openrouter.ai/api/v1" not in str(team_audit.audit_metadata)


def test_team_member_model_provider_update_does_not_initialize_runtime_sessions() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenAI Reviewer",
            "provider": "openai",
            "api_key": "sk-openai",
            "default_model": "gpt-4.1-mini",
        },
    )
    agent = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "Runtime Quiet Agent", "role": "developer"},
    )
    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={"name": "Runtime Quiet Team", "team_type": "company"},
    )
    member = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.json()['id']}/members",
        headers=_headers(owner.id),
        json={"agent_profile_id": agent.json()["id"], "team_role": "developer"},
    )
    assert credential.status_code == 201
    assert agent.status_code == 201
    assert team.status_code == 201
    assert member.status_code == 201
    team_id = UUID(team.json()["id"])
    assert (
        session.scalar(
            select(func.count(AgentSessionMetadata.id)).where(
                AgentSessionMetadata.workspace_id == workspace.id,
                AgentSessionMetadata.agent_team_id == team_id,
            )
        )
        == 0
    )
    updated = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team_id}/members/{member.json()['id']}/model-provider",
        headers=_headers(owner.id),
        json={
            "model_provider_credential_id": credential.json()["id"],
            "model": "workspace-default",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["model_provider_credential_id"] == credential.json()["id"]
    assert (
        session.scalar(
            select(func.count(AgentSessionMetadata.id)).where(
                AgentSessionMetadata.workspace_id == workspace.id,
                AgentSessionMetadata.agent_team_id == team_id,
            )
        )
        == 0
    )
    provider_message = session.scalar(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == team_id,
            AgentMessage.message_type == "team.runtime.model_provider.updated",
        )
    )
    assert provider_message is not None
    assert provider_message.thread_id is not None
    assert session.get(AgentMessageThread, provider_message.thread_id) is not None


def test_model_provider_credentials_can_be_updated_rotated_defaulted_and_disabled() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "First",
            "provider": "openai",
            "api_key": "sk-first",
            "default_model": "gpt-4.1",
            "is_default": True,
        },
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Second",
            "provider": "openai-compatible",
            "api_key": "sk-second",
            "base_url": "https://llm.example.test/v1",
            "default_model": "provider/default",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201
    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{second.json()['id']}",
        headers=_headers(owner.id),
        json={
            "name": "Second Updated",
            "default_model": "provider/new-default",
            "model_api": "responses",
        },
    )
    rotated = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{second.json()['id']}/rotate-key",
        headers=_headers(owner.id),
        json={"api_key": "sk-second-rotated"},
    )
    defaulted = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{second.json()['id']}/set-default",
        headers=_headers(owner.id),
    )
    disabled = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/{first.json()['id']}/disable",
        headers=_headers(owner.id),
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials", headers=_headers(owner.id)
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Second Updated"
    assert updated.json()["model_api"] == "responses"
    assert updated.json()["budget_metadata"]["model_api"] == "responses"
    assert updated.json()["base_url_configured"] is True
    assert updated.json()["base_url_host"] == "llm.example.test"
    assert updated.json()["model_capability"] is None
    assert rotated.status_code == 200
    assert rotated.json()["api_key_fingerprint"] != second.json()["api_key_fingerprint"]
    assert rotated.json()["secret_metadata"]["rotation_state"] == "current"
    assert rotated.json()["secret_metadata"]["raw_secret_exposed"] is False
    assert "api_key" not in rotated.json()
    assert defaulted.status_code == 200
    assert defaulted.json()["is_default"] is True
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert by_id[first.json()["id"]]["is_default"] is False
    assert by_id[first.json()["id"]]["status"] == "disabled"
    assert by_id[second.json()["id"]]["is_default"] is True
    assert by_id[second.json()["id"]]["base_url_host"] == "llm.example.test"
    assert by_id[second.json()["id"]]["model_api"] == "responses"
    assert by_id[second.json()["id"]]["model_capability"] == updated.json()["model_capability"]


def test_model_provider_usage_audit_api_is_scoped_and_redacted() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-provider@example.com", slug="other-provider"
    )
    session.add_all(
        [
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(owner.id),
                user_id=owner.id,
                action="model_provider.used",
                target_type="agent_run",
                target_id="run-1",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "task_id": "task-1",
                    "task_step_id": "step-1",
                    "agent_profile_id": "agent-1",
                    "provider": "openai-compatible",
                    "model": "backup-model",
                    "model_api": "responses",
                    "credential_id": "credential-1",
                    "api_key": "sk-secret",
                    "base_url": "https://secret.example.test/v1",
                },
            ),
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(owner.id),
                user_id=owner.id,
                action="model_provider.request_failed",
                target_type="agent_run",
                target_id="run-2",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "reason": {"code": "RuntimeError", "message": "primary unavailable"},
                    "failed_provider": {
                        "provider": "openai",
                        "model": "primary-model",
                        "model_api": "chat_completions",
                        "credential_id": "credential-2",
                        "api_key": "sk-primary",
                        "base_url": "https://primary.example.test/v1",
                    },
                },
            ),
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=str(owner.id),
                user_id=owner.id,
                action="model_provider.request_failed",
                target_type="agent_run",
                target_id="run-3",
                created_at=datetime.now(UTC),
                audit_metadata={
                    "task_id": "task-3",
                    "task_step_id": "step-3",
                    "agent_profile_id": "agent-3",
                    "provider": "openai-compatible",
                    "model": "gpt-5.5",
                    "model_api": "chat_completions",
                    "credential_id": "credential-3",
                    "reason": {
                        "code": "InternalServerError",
                        "message": "upstream failed with sk-hidden token",
                    },
                },
            ),
            AuditEvent(
                workspace_id=other_workspace.id,
                actor_type="user",
                actor_id=str(other_owner.id),
                user_id=other_owner.id,
                action="model_provider.used",
                target_type="agent_run",
                target_id="foreign-run",
                created_at=datetime.now(UTC),
                audit_metadata={"model": "foreign-model"},
            ),
        ]
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/usage-audit",
        headers=_headers(owner.id),
    )
    failed_only = client.get(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials/usage-audit?action=model_provider.request_failed",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert {item["run_id"] for item in payload["items"]} == {"run-1", "run-2", "run-3"}
    serialized = str(payload)
    assert "sk-secret" not in serialized
    assert "sk-primary" not in serialized
    assert "sk-hidden" not in serialized
    assert "secret.example.test" not in serialized
    assert "primary.example.test" not in serialized
    used = next(item for item in payload["items"] if item["run_id"] == "run-1")
    assert used["provider"] == "openai-compatible"
    assert used["model"] == "backup-model"
    assert used["model_api"] == "responses"
    assert used["credential_id"] == "credential-1"
    failed_provider = next(item for item in payload["items"] if item["run_id"] == "run-2")
    assert failed_provider["failed_provider"] == {
        "provider": "openai",
        "model": "primary-model",
        "model_api": "chat_completions",
        "credential_id": "credential-2",
    }
    assert failed_only.status_code == 200
    assert failed_only.json()["total"] == 2
    failed = next(item for item in failed_only.json()["items"] if item["run_id"] == "run-3")
    assert failed["run_id"] == "run-3"
    assert failed["provider"] == "openai-compatible"
    assert failed["model"] == "gpt-5.5"
    assert failed["model_api"] == "chat_completions"
    assert failed["credential_id"] == "credential-3"
    assert failed["reason"] == {"code": "InternalServerError", "message": "[redacted]"}
