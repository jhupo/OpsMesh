from datetime import UTC, datetime
from uuid import UUID

from test_workspace_api import _client, _headers, _seed_workspace

from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent


def test_model_provider_operations_exposes_agent_run_and_fallback_diagnostics() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    primary = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "OpenAI primary",
            "provider": "openai",
            "api_key": "sk-primary-operations",
            "default_model": "gpt-4.1",
            "model_api": "responses",
            "is_default": True,
            "budget_metadata": {
                "limits": {"monthly_tokens": 10000},
                "usage": {"monthly_tokens": 2500},
                "remaining": {"monthly_tokens": 7500},
            },
        },
    )
    backup = client.post(
        f"/api/v1/workspaces/{workspace.id}/model-provider-credentials",
        headers=_headers(owner.id),
        json={
            "name": "Anthropic backup",
            "provider": "anthropic",
            "api_key": "sk-backup-operations",
            "default_model": "claude-sonnet-4-6",
        },
    )
    agent_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={
            "name": "Operations agent",
            "role": "operator",
            "model": "workspace-default",
            "model_provider_credential_id": primary.json()["id"],
        },
    )
    assert primary.status_code == 201
    assert backup.status_code == 201
    assert agent_response.status_code == 201

    workspace.settings = {
        "model_provider_fallback": {
            "enabled": True,
            "retry_error_codes": ["RuntimeError"],
            "candidates": [
                {
                    "credential_id": backup.json()["id"],
                    "model": "claude-sonnet-4-6",
                    "model_api": "anthropic_messages",
                }
            ],
        }
    }
    run = AgentRun(
        workspace_id=workspace.id,
        agent_profile_id=UUID(agent_response.json()["id"]),
        status="running",
        model="gpt-4.1",
        input={
            "authorization_snapshot": {
                "model_provider": {
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "model_api": "responses",
                    "credential_id": primary.json()["id"],
                    "source": "agent_override",
                }
            }
        },
    )
    session.add(run)
    session.flush()
    now = datetime.now(UTC)
    session.add(
        RunEvent(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            event_type="model_provider.fallback_selected",
            sequence=1,
            message="fallback",
            event_metadata={
                "failed_provider": {"provider": "openai", "api_key": "sk-hidden"},
                "model_provider": {"provider": "anthropic"},
            },
            created_at=now,
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/model-providers",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "agent_count": 1,
        "ready_agent_count": 0,
        "degraded_agent_count": 1,
        "blocked_agent_count": 0,
        "credential_count": 2,
        "active_credential_count": 2,
        "healthy_credential_count": 0,
        "active_run_count": 1,
        "fallback_enabled": True,
        "fallback_selected_run_count": 1,
        "fallback_unavailable_run_count": 0,
    }
    assert body["agents"][0]["provider"] == "openai"
    assert body["agents"][0]["protocol"] == "responses"
    assert body["agents"][0]["budget"] == {
        "status": "ok",
        "exhausted": False,
        "limits": {"monthly_tokens": 10000},
        "usage": {"monthly_tokens": 2500},
        "remaining": {"monthly_tokens": 7500},
    }
    assert body["fallback"]["candidates"][0]["provider"] == "anthropic"
    assert body["fallback"]["candidates"][0]["selectable"] is True
    assert body["runs"][0]["fallback_status"] == "selected"
    assert body["runs"][0]["provider"] == "openai"
    assert body["runs"][0]["fallback"]["failed_provider"]["api_key"] == "[redacted]"
    assert "sk-hidden" not in str(body)
    assert "encrypted_api_key" not in str(body)


def test_model_provider_operations_is_workspace_scoped_and_admin_only() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer, other_workspace = _seed_workspace(
        session,
        role="viewer",
        email="provider-operations-viewer@example.com",
        slug="provider-operations-viewer",
    )

    assert client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/model-providers",
        headers=_headers(viewer.id),
    ).status_code == 403
    assert client.get(
        f"/api/v1/workspaces/{other_workspace.id}/operations/model-providers",
        headers=_headers(owner.id),
    ).status_code == 403
