"""Workspace-scoped product API verification on PostgreSQL."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.agents.messages.models import AgentMessage, AgentMessageThread
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.agents.sessions.models import (
    AgentSessionMetadata,
    SDKSessionMessageRecord,
    SDKSessionRecord,
)
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.models import ResourceGrant, SecuredResource
from opsmesh.identity.authorization.permissions import (
    ROLE_PERMISSIONS,
    WorkspaceAction,
    WorkspaceRole,
)
from opsmesh.identity.authorization.resources import ResourceAction, ResourceKind
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.scheduling.models import WorkspaceScheduledJob
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.memory.models import WorkspaceMemoryEntry, memory_content_fingerprint
from opsmesh.runtime.instances.models import RuntimeTemplate, WorkspaceRuntime
from opsmesh.runtime.spaces.models import RuntimeSpace, RuntimeSpaceQuota, RuntimeSpaceReservation
from opsmesh.runtime.temporal.contracts import ExecutionKind
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.teams.observation.timeline import TeamTimelineService, TimelineFilters
from opsmesh.teams.operations.console import TeamOperationsConsoleService
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.team_orchestration import run_team_request
from tests.fixtures.workspace_api import _client, _headers, _pending_dispatch_count, _seed_workspace


def test_team_runtime_controls_create_sessions_mailbox_and_workspace_runtime() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, _ = _seed_workspace(
        session, role="owner", email="other-runtime@example.com", slug="other-runtime"
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    template = RuntimeTemplate(
        name="team-runtime-template",
        image="python@sha256:" + "0" * 64,
        created_at=datetime.now(UTC),
    )
    session.add_all([manager, developer, template])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Runtime Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id, name="Runtime Team Container", status="active"
    )
    session.add_all([team, runtime])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    session.commit()
    initial = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime", headers=_headers(owner.id)
    )
    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={"reason": "open the company", "metadata": {"api_key": "sk-runtime"}},
    )
    paused = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/pause",
        headers=_headers(owner.id),
        json={"reason": "operator pause"},
    )
    continued = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/continue",
        headers=_headers(owner.id),
        json={"instruction": "continue from the last team state"},
    )
    ensured = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(owner.id),
        json={
            "template_id": str(template.id),
            "name": "Runtime Team Persistent Container",
            "reason": "ensure durable team container",
            "metadata": {"api_key": "sk-ensure-runtime"},
        },
    )
    ensured_again = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(owner.id),
        json={"template_id": str(template.id), "reason": "reuse durable team container"},
    )
    bound = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/bind",
        headers=_headers(owner.id),
        json={
            "workspace_runtime_id": str(runtime.id),
            "reason": "bind stable container",
            "metadata": {"token": "hidden-runtime-token"},
        },
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime",
        headers=_headers(other_owner.id),
    )
    assert initial.status_code == 200
    assert initial.json()["status"] == "stopped"
    assert initial.json()["runtime_health"] == "stopped"
    assert initial.json()["last_advancement"] is None
    assert initial.json()["last_message_at"] is None
    assert initial.json()["thread_id"] is None
    assert initial.json()["team_session_id"] is None
    assert initial.json()["team_session_key"] is None
    assert initial.json()["member_session_count"] == 0
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert started.json()["runtime_health"] == "healthy"
    assert started.json()["thread_id"] is not None
    assert started.json()["team_session_id"] is not None
    assert started.json()["team_session_key"] is not None
    assert started.json()["member_session_count"] == 2
    assert started.json()["last_message_at"] is not None
    assert "sk-runtime" not in str(started.json())
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["runtime_health"] == "paused"
    assert continued.status_code == 200
    assert continued.json()["status"] == "running"
    assert continued.json()["runtime_health"] == "healthy"
    assert ensured.status_code == 200
    assert ensured.json()["status"] == "running"
    assert ensured.json()["runtime_status"] == "active"
    ensured_runtime_id = ensured.json()["workspace_runtime_id"]
    assert ensured_runtime_id != str(runtime.id)
    assert "sk-ensure-runtime" not in str(ensured.json())
    assert ensured_again.status_code == 200
    assert ensured_again.json()["workspace_runtime_id"] == ensured_runtime_id
    assert bound.status_code == 200
    assert bound.json()["workspace_runtime_id"] == str(runtime.id)
    assert bound.json()["runtime_status"] == "active"
    assert bound.json()["runtime_health"] == "healthy"
    assert "hidden-runtime-token" not in str(bound.json())
    assert forbidden.status_code == 403
    session.expire_all()
    runtime_thread = session.get(AgentMessageThread, UUID(started.json()["thread_id"]))
    assert runtime_thread is not None
    assert runtime_thread.agent_team_id == team.id
    messages = session.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.workspace_id == workspace.id, AgentMessage.thread_id == runtime_thread.id
        )
        .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
    ).all()
    assert [message.message_type for message in messages] == [
        "team.runtime.started",
        "team.runtime.paused",
        "team.runtime.continued",
        "team.runtime.ensured",
        "team.runtime.ensured",
        "team.runtime.bound",
    ]
    assert {message.agent_team_id for message in messages} == {team.id}
    session.refresh(runtime)
    ensured_runtime = session.get(WorkspaceRuntime, UUID(ensured_runtime_id))
    assert ensured_runtime is not None
    sessions = session.scalars(
        select(AgentSessionMetadata).where(
            AgentSessionMetadata.workspace_id == workspace.id,
            AgentSessionMetadata.agent_team_id == team.id,
        )
    ).all()
    assert {item.scope_type for item in sessions} == {"team_runtime", "team_agent"}
    assert {item.agent_profile_id for item in sessions if item.scope_type == "team_agent"} == {
        manager.id,
        developer.id,
    }


def test_team_session_controls_manage_runtime_and_member_sessions() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session, role="owner", email="other-team-sessions@example.com", slug="other-team-sessions"
    )
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Session Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    other_team = AgentTeam(
        workspace_id=other_workspace.id, name="Foreign Session Team", team_type="software"
    )
    session.add_all([manager, developer, team, other_team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    session.commit()
    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={"reason": "start team sessions"},
    )
    assert started.status_code == 200
    team_session_id = UUID(started.json()["team_session_id"])
    team_session = session.get(AgentSessionMetadata, team_session_id)
    assert team_session is not None
    session.add(SDKSessionRecord(session_id=team_session.session_key))
    session.flush()
    for content in ("old", "middle", "recent"):
        session.add(
            SDKSessionMessageRecord(
                session_id=team_session.session_key,
                message_data=json.dumps(
                    {
                        "role": "assistant",
                        "content": f"{content} session content token sk-team-session-secret",
                    }
                ),
            )
        )
    session.commit()
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions", headers=_headers(owner.id)
    )
    detail = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}",
        headers=_headers(owner.id),
    )
    frozen = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}/freeze",
        headers=_headers(owner.id),
    )
    active = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}/activate",
        headers=_headers(owner.id),
    )
    cleared = client.delete(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions/{team_session.id}/items",
        headers=_headers(owner.id),
    )
    missing_team_session = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/sessions/{team_session.id}",
        headers=_headers(owner.id),
    )
    foreign_team_session = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{other_team.id}/sessions/{team_session.id}",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/sessions",
        headers=_headers(other_owner.id),
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 3
    assert {item["scope_type"] for item in listed.json()["items"]} == {"team_runtime", "team_agent"}
    assert detail.status_code == 200
    assert detail.json()["session"]["scope_type"] == "team_runtime"
    assert detail.json()["session"]["agent_team_id"] == str(team.id)
    assert len(detail.json()["items"]) == 3
    assert "sk-team-session-secret" not in json.dumps(detail.json())
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "frozen"
    assert active.status_code == 200
    assert active.json()["status"] == "active"
    assert cleared.status_code == 200
    assert cleared.json()["deleted_item_count"] == 3
    assert missing_team_session.status_code == 403
    assert foreign_team_session.status_code == 403
    assert forbidden.status_code == 403


def test_team_runtime_actions_require_manage_runtime_for_write_only_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    writer = User(email="runtime-writer@example.com", display_name="Runtime Writer")
    session.add(writer)
    session.flush()
    session.add(WorkspaceMember(workspace=workspace, user=writer, role="operator"))
    template = RuntimeTemplate(
        name="permission-runtime-template",
        image="python@sha256:" + "0" * 64,
        created_at=datetime.now(UTC),
    )
    team = AgentTeam(
        workspace_id=workspace.id, name="Permission Runtime Team", team_type="software"
    )
    session.add_all([template, team])
    session.flush()
    session.add(
        SecuredResource(
            workspace_id=workspace.id,
            resource_kind=ResourceKind.TEAM.value,
            resource_id=team.id,
            owner_user_id=owner.id,
        )
    )
    session.flush()
    session.add_all(
        [
            ResourceGrant(
                workspace_id=workspace.id,
                resource_kind=ResourceKind.TEAM.value,
                resource_id=team.id,
                user_id=writer.id,
                action=action.value,
            )
            for action in (ResourceAction.READ, ResourceAction.CONTROL)
        ]
    )
    session.commit()
    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        WorkspaceRole.OPERATOR,
        {
            WorkspaceAction.READ,
            WorkspaceAction.WRITE,
            WorkspaceAction.APPROVE,
            WorkspaceAction.OPERATE,
        },
    )
    ensure_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(writer.id),
        json={},
    )
    bind_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/bind",
        headers=_headers(writer.id),
        json={"workspace_runtime_id": str(uuid4())},
    )
    apply_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(writer.id),
        json={"dry_run": False, "actions": ["ensure_team_runtime"]},
    )
    dry_run_response = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center/actions/apply",
        headers=_headers(writer.id),
        json={"dry_run": True, "actions": ["ensure_team_runtime"]},
    )
    owner_ensure = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/ensure",
        headers=_headers(owner.id),
        json={"template_id": str(template.id)},
    )
    assert ensure_response.status_code == 403
    assert bind_response.status_code == 403
    assert apply_response.status_code == 403
    assert dry_run_response.status_code == 200
    assert owner_ensure.status_code == 200
    assert owner_ensure.json()["runtime_status"] == "active"


def test_team_stop_and_resume_preserve_shared_execution_environment() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Bound Runtime Team",
        team_type="software",
        default_task_policy={"team_runtime": {"status": "running"}},
    )
    runtime = WorkspaceRuntime(workspace_id=workspace.id, name="Bound Runtime", status="active")
    session.add_all([team, runtime])
    session.commit()
    bound = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/bind",
        headers=_headers(owner.id),
        json={"workspace_runtime_id": str(runtime.id), "reason": "bind running runtime"},
    )
    stopped = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/stop",
        headers=_headers(owner.id),
        json={"reason": "stop the bound runtime"},
    )
    resumed = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/resume",
        headers=_headers(owner.id),
        json={"reason": "resume the bound runtime"},
    )
    assert bound.status_code == 200
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["runtime_status"] == "active"
    assert stopped.json()["metadata"]["workspace_runtime_control"]["mode"] == "team_progress_paused"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"
    assert resumed.json()["runtime_status"] == "active"
    assert (
        resumed.json()["metadata"]["workspace_runtime_control"]["mode"] == "native_sandbox_binding"
    )
    session.expire_all()
    stored_runtime = session.get(WorkspaceRuntime, runtime.id)
    assert stored_runtime is not None
    assert stored_runtime.status == "active"


def test_team_runtime_and_command_center_expose_policy_and_memory_summary() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session, role="owner", email="other-memory-owner@example.com", slug="other-memory-owner"
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="gpt-4.1",
        model_settings={"temperature": 0.2, "api_key": "sk-manager-model-secret"},
        tool_policy={
            "allowed_tools": ["planner"],
            "headers": {"authorization": "Bearer manager-tool-secret"},
        },
        runtime_policy={
            "runtime": "docker",
            "env": {"OPENAI_API_KEY": "sk-manager-runtime-secret"},
        },
        approval_policy={"requires_approval": True, "token": "manager-approval-token"},
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="gpt-4.1-mini",
        tool_policy={"allowed_tools": ["code_search"]},
        runtime_policy={"cpu": 2},
        approval_policy={"requires_approval": False},
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Runtime Space",
        scope="team",
        policy={"max_active_runs": 4, "api_key": "sk-runtime-space-secret"},
        storage_metadata={"persistent": True},
        cleanup_metadata={"ttl_hours": 24},
    )
    session.add_all([manager, developer, runtime_space])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Memory Team",
        team_type="software",
        description="Runs the company operating cadence.",
        manager_agent_profile_id=manager.id,
        runtime_space_id=runtime_space.id,
        coordination_rules={"daily_sync": "async", "handoff": "manager_review"},
        default_task_policy={"priority": 4, "approval_required": True},
    )
    other_team = AgentTeam(workspace_id=workspace.id, name="Other Memory Team", team_type="support")
    session.add_all([team, other_team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                department="Operations",
                max_concurrent_tasks=2,
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                department="Engineering",
                max_concurrent_tasks=3,
                order_index=2,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="workspace",
                scope_id=str(workspace.id),
                created_by_user_id=owner.id,
                entry_type="operating_note",
                title="Customer escalation rule",
                content="Escalate enterprise renewal blockers before implementation starts.",
                content_fingerprint=memory_content_fingerprint(
                    "Customer escalation rule",
                    "Escalate enterprise renewal blockers before implementation starts.",
                ),
                tags=["operating-policy", "renewal"],
                visibility_scope="company",
                importance=7,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="workspace",
                scope_id=str(workspace.id),
                created_by_user_id=owner.id,
                entry_type="operating_note",
                title="Workspace deployment preference",
                content="Prefer staged deploys for workspace-wide releases.",
                content_fingerprint=memory_content_fingerprint(
                    "Workspace deployment preference",
                    "Prefer staged deploys for workspace-wide releases.",
                ),
                tags=["workspace"],
                visibility_scope="workspace",
                importance=6,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="workspace",
                scope_id=str(workspace.id),
                created_by_user_id=owner.id,
                entry_type="operating_note",
                title="Shared incident runbook",
                content="Shared scope memories are available to every team in the workspace.",
                content_fingerprint=memory_content_fingerprint(
                    "Shared incident runbook",
                    "Shared scope memories are available to every team in the workspace.",
                ),
                tags=["shared"],
                visibility_scope="shared",
                importance=5,
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="team",
                scope_id=str(team.id),
                created_by_user_id=owner.id,
                source_type="agent_team",
                source_id=str(team.id),
                entry_type="team_memory",
                title="Team release ritual",
                content=(
                    "Memory Team ships behind flags and records rollout owners. Do not expose "
                    "sk-memory-content-secret or token=plain-token-secret."
                ),
                content_fingerprint=memory_content_fingerprint(
                    "Team release ritual",
                    (
                        "Memory Team ships behind flags and records rollout owners. Do not expose "
                        "sk-memory-content-secret or token=plain-token-secret."
                    ),
                ),
                tags=["team-memory", f"team:{team.id}"],
                visibility_scope="team",
                importance=9,
                memory_metadata={
                    "agent_team_id": str(team.id),
                    "api_key": "sk-memory-secret",
                    "note": "Authorization: Bearer memory-metadata-secret",
                },
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="team",
                scope_id=str(team.id),
                created_by_user_id=owner.id,
                entry_type="team_memory",
                title="Metadata team memory",
                content="Team metadata uses the canonical agent_team_id identifier.",
                content_fingerprint=memory_content_fingerprint(
                    "Metadata team memory",
                    "Team metadata uses the canonical agent_team_id identifier.",
                ),
                tags=["team-memory"],
                visibility_scope="team",
                importance=8,
                memory_metadata={"agent_team_id": str(team.id)},
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="team",
                scope_id=str(other_team.id),
                created_by_user_id=owner.id,
                source_type="agent_team",
                source_id=str(other_team.id),
                entry_type="team_memory",
                title="Other team private memory",
                content="This other team detail must not enter the Memory Team runtime.",
                content_fingerprint=memory_content_fingerprint(
                    "Other team private memory",
                    "This other team detail must not enter the Memory Team runtime.",
                ),
                tags=["team-memory", f"team:{other_team.id}"],
                visibility_scope="team",
                importance=10,
                memory_metadata={"agent_team_id": str(other_team.id)},
            ),
            WorkspaceMemoryEntry(
                workspace_id=other_workspace.id,
                scope_type="workspace",
                scope_id=str(other_workspace.id),
                entry_type="operating_note",
                title="Foreign workspace memory",
                content="This foreign workspace detail must not be visible.",
                content_fingerprint=memory_content_fingerprint(
                    "Foreign workspace memory", "This foreign workspace detail must not be visible."
                ),
                tags=["foreign"],
                visibility_scope="company",
                importance=10,
            ),
        ]
    )
    session.add_all(
        [
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="team",
                scope_id=str(other_team.id),
                created_by_user_id=owner.id,
                source_type="agent_team",
                source_id=str(other_team.id),
                entry_type="team_memory",
                title=f"Other team private memory {index}",
                content="This high-priority other team detail must not displace visible memory.",
                content_fingerprint=memory_content_fingerprint(
                    f"Other team private memory {index}",
                    "This high-priority other team detail must not displace visible memory.",
                ),
                tags=["team-memory", f"team:{other_team.id}"],
                visibility_scope="team",
                importance=100 + index,
                memory_metadata={"agent_team_id": str(other_team.id)},
            )
            for index in range(205)
        ]
    )
    session.commit()
    runtime = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime", headers=_headers(owner.id)
    )
    command_center = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/command-center",
        headers=_headers(owner.id),
    )
    assert runtime.status_code == 200
    runtime_body = runtime.json()
    assert runtime_body["operating_policy"]["coordination_rules"] == {
        "daily_sync": "async",
        "handoff": "manager_review",
    }
    assert runtime_body["operating_policy"]["default_task_policy"] == {
        "priority": 4,
        "approval_required": True,
    }
    assert runtime_body["operating_policy"]["runtime_space"] == {
        "id": str(runtime_space.id),
        "name": "Team Runtime Space",
        "scope": "team",
        "status": "active",
        "default_runtime_template_id": None,
        "policy": {"max_active_runs": 4, "api_key": "[redacted]"},
        "storage_metadata": {"persistent": True},
        "cleanup_metadata": {"ttl_hours": 24},
    }
    assert runtime_body["operating_policy"]["staffing"]["active_member_count"] == 2
    assert runtime_body["operating_policy"]["staffing"]["max_concurrent_tasks"] == 5
    assert runtime_body["operating_policy"]["staffing"]["roles"] == ["developer", "project_manager"]
    member_policies = {
        member["agent_role"]: member["agent_policy"]
        for member in runtime_body["operating_policy"]["members"]
    }
    assert member_policies["project_manager"]["model"] == "gpt-4.1"
    assert member_policies["project_manager"]["model_settings"] == {
        "temperature": 0.2,
        "api_key": "[redacted]",
    }
    assert member_policies["project_manager"]["tool_policy"] == {
        "allowed_tools": ["planner"],
        "headers": "[redacted]",
    }
    assert member_policies["project_manager"]["runtime_policy"] == {
        "runtime": "docker",
        "env": {"OPENAI_API_KEY": "[redacted]"},
    }
    assert member_policies["project_manager"]["approval_policy"] == {
        "requires_approval": True,
        "token": "[redacted]",
    }
    assert runtime_body["memory_summary"]["active_entry_count"] == 5
    assert runtime_body["memory_summary"]["team_entry_count"] == 2
    assert runtime_body["memory_summary"]["shared_entry_count"] == 3
    assert runtime_body["memory_summary"]["scope_counts"] == {
        "company": 1,
        "shared": 1,
        "team": 2,
        "workspace": 1,
    }
    memory_titles = {entry["title"] for entry in runtime_body["memory_summary"]["entries"]}
    assert memory_titles == {
        "Customer escalation rule",
        "Metadata team memory",
        "Shared incident runbook",
        "Team release ritual",
        "Workspace deployment preference",
    }
    team_entry = next(
        entry
        for entry in runtime_body["memory_summary"]["entries"]
        if entry["title"] == "Team release ritual"
    )
    assert "[redacted]" in team_entry["snippet"]
    assert team_entry["metadata"]["api_key"] == "[redacted]"
    assert team_entry["metadata"]["note"] == "[redacted]"
    assert "sk-memory-secret" not in str(runtime_body)
    assert "sk-memory-content-secret" not in str(runtime_body)
    assert "plain-token-secret" not in str(runtime_body)
    assert "memory-metadata-secret" not in str(runtime_body)
    assert "sk-manager-model-secret" not in str(runtime_body)
    assert "manager-tool-secret" not in str(runtime_body)
    assert "sk-manager-runtime-secret" not in str(runtime_body)
    assert "manager-approval-token" not in str(runtime_body)
    assert "sk-runtime-space-secret" not in str(runtime_body)
    assert "runtime-network-token" not in str(runtime_body)
    assert "Other team private memory" not in str(runtime_body)
    assert "Foreign workspace memory" not in str(runtime_body)
    assert command_center.status_code == 200
    command_body = command_center.json()
    assert command_body["operating_policy"] == runtime_body["operating_policy"]
    assert command_body["memory_summary"]["active_entry_count"] == 5
    assert (
        command_body["memory_summary"]["scope_counts"]
        == runtime_body["memory_summary"]["scope_counts"]
    )
    assert command_body["runtime"]["operating_policy"] == runtime_body["operating_policy"]
    assert command_body["runtime"]["memory_summary"]["team_entry_count"] == 2
    assert (
        command_body["runtime"]["memory_summary"]["scope_counts"]
        == runtime_body["memory_summary"]["scope_counts"]
    )
    assert command_body["summary"]["memory_entry_count"] == 5
    assert command_body["summary"]["team_memory_entry_count"] == 2
    assert "Other team private memory" not in str(command_body)
    assert "sk-memory-secret" not in str(command_body)


def test_team_project_space_exposes_runtime_capacity_and_outputs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session, role="owner", email="other-project-space@example.com", slug="other-project-space"
    )
    now = datetime.now(UTC)
    manager = AgentProfile(
        workspace_id=workspace.id, name="Delivery Manager", role="project_manager", model="gpt-4.1"
    )
    developer = AgentProfile(
        workspace_id=workspace.id, name="Builder", role="developer", model="gpt-4.1-mini"
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Project Space",
        scope="team",
        policy={"space_tier": "standard", "api_key": "sk-project-space-secret"},
        storage_metadata={"max_storage_mb": 25600, "token": "storage-secret-token"},
        cleanup_metadata={"completed_task_retention_days": 90},
    )
    task_runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Task Space",
        scope="task",
        policy={"space_tier": "small"},
        storage_metadata={"max_storage_mb": 5120},
        cleanup_metadata={"completed_task_retention_days": 30},
    )
    session.add_all([manager, developer, runtime_space, task_runtime_space])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Project Space Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        runtime_space_id=runtime_space.id,
        coordination_rules={"cadence": "async"},
        default_task_policy={"priority": 5},
    )
    other_team = AgentTeam(workspace_id=workspace.id, name="Other Team", team_type="support")
    session.add_all([team, other_team])
    session.flush()
    manager_member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=manager.id,
        team_role="project_manager",
        department="Product",
        position_title="Project Manager",
        max_concurrent_tasks=2,
        order_index=1,
    )
    developer_member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=developer.id,
        team_role="developer",
        department="Engineering",
        position_title="Senior Engineer",
        max_concurrent_tasks=3,
        order_index=2,
    )
    session.add_all([manager_member, developer_member])
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        runtime_space_id=task_runtime_space.id,
        title="Ship project-space dashboard",
        description="Expose mature project-space governance.",
        status="running",
        priority=9,
    )
    completed_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        runtime_space_id=runtime_space.id,
        title="Completed old task",
        description="Hidden by default.",
        status="completed",
        priority=1,
        completed_at=now,
    )
    foreign_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=other_team.id,
        title="Foreign team task",
        description="Must not leak.",
        status="running",
        priority=10,
    )
    session.add_all([task, completed_task, foreign_task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        runtime_space_id=runtime_space.id,
        work_package_id="engineering-build",
        required_role="developer",
        expected_artifacts=["build_report"],
        title="Build project-space service",
        description="Aggregate task outputs into project space.",
        status="running",
        order_index=1,
    )
    manager_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        runtime_space_id=runtime_space.id,
        work_package_id="manager-review",
        required_role="project_manager",
        expected_artifacts=[],
        title="Review delivery",
        description="Check output.",
        status="queued",
        order_index=2,
    )
    foreign_step = TaskStep(
        workspace_id=workspace.id,
        task_id=foreign_task.id,
        assigned_agent_profile_id=developer.id,
        title="Foreign work",
        description="Must not leak.",
        status="running",
        order_index=1,
    )
    session.add_all([step, manager_step, foreign_step])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=developer.id,
        runtime_space_id=runtime_space.id,
        status=RunStatus.RUNNING.value,
        input={"prompt": "build"},
        started_at=now,
    )
    foreign_run = AgentRun(
        workspace_id=workspace.id,
        task_id=foreign_task.id,
        task_step_id=foreign_step.id,
        agent_profile_id=developer.id,
        status=RunStatus.RUNNING.value,
    )
    session.add_all([run, foreign_run])
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_run_id=run.id,
        agent_profile_id=developer.id,
        work_package_id="engineering-build",
        artifact_type="build_report",
        filename="build-report.md",
        content_type="text/markdown",
        size_bytes=2048,
        checksum_sha256="a" * 64,
        storage_key="artifacts/build-report.md",
        review_status="pending",
        created_at=now,
    )
    foreign_artifact = Artifact(
        workspace_id=workspace.id,
        task_id=foreign_task.id,
        task_step_id=foreign_step.id,
        agent_run_id=foreign_run.id,
        agent_profile_id=developer.id,
        artifact_type="foreign",
        filename="foreign.md",
        content_type="text/markdown",
        size_bytes=9999,
        checksum_sha256="b" * 64,
        storage_key="artifacts/foreign.md",
        created_at=now,
    )
    session.add_all([artifact, foreign_artifact])
    session.flush()
    session.add_all(
        [
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                quota_key="active_runs",
                limit_value=8,
                reserved_value=1,
                unit="count",
            ),
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=task_runtime_space.id,
                quota_key="active_runs",
                limit_value=2,
                reserved_value=0,
                unit="count",
            ),
            RuntimeSpaceReservation(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                task_id=task.id,
                task_step_id=step.id,
                agent_run_id=run.id,
                reservation_key="run-reservation",
                resource_usage={"active_runs": 1, "storage_bytes": 512},
                status="active",
            ),
            WorkspaceFile(
                workspace_id=workspace.id,
                uploaded_by_user_id=owner.id,
                filename="brief.md",
                content_type="text/markdown",
                size_bytes=1024,
                checksum_sha256="c" * 64,
                storage_key="files/brief.md",
                file_metadata={
                    "agent_team_id": str(team.id),
                    "task_id": str(task.id),
                    "runtime_space_id": str(runtime_space.id),
                    "token": "file-hidden-token",
                },
            ),
            WorkspaceFile(
                workspace_id=workspace.id,
                uploaded_by_user_id=owner.id,
                filename="foreign.md",
                content_type="text/markdown",
                size_bytes=8192,
                checksum_sha256="d" * 64,
                storage_key="files/foreign.md",
                file_metadata={
                    "agent_team_id": str(other_team.id),
                    "task_id": str(foreign_task.id),
                },
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="team",
                scope_id=str(team.id),
                created_by_user_id=owner.id,
                source_type="agent_team",
                source_id=str(team.id),
                entry_type="team_memory",
                title="Team project-space ritual",
                content="Outputs land in the project space.",
                content_fingerprint=memory_content_fingerprint(
                    "Team project-space ritual", "Outputs land in the project space."
                ),
                visibility_scope="team",
                importance=9,
                memory_metadata={"agent_team_id": str(team.id)},
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="task",
                scope_id=str(task.id),
                created_by_agent_profile_id=developer.id,
                created_by_agent_run_id=run.id,
                source_type="agent_run",
                source_id=str(run.id),
                entry_type="run_summary",
                title="Build summary",
                content="Implemented aggregation.",
                content_fingerprint=memory_content_fingerprint(
                    "Build summary", "Implemented aggregation."
                ),
                visibility_scope="team",
                importance=7,
                memory_metadata={"task_id": str(task.id)},
            ),
            WorkspaceMemoryEntry(
                workspace_id=workspace.id,
                scope_type="team",
                scope_id=str(other_team.id),
                source_type="agent_team",
                source_id=str(other_team.id),
                entry_type="team_memory",
                title="Foreign team memory",
                content="Must not leak.",
                content_fingerprint=memory_content_fingerprint(
                    "Foreign team memory", "Must not leak."
                ),
                visibility_scope="team",
                importance=100,
                memory_metadata={"agent_team_id": str(other_team.id)},
            ),
            WorkspaceMemoryEntry(
                workspace_id=other_workspace.id,
                scope_type="workspace",
                scope_id=str(other_workspace.id),
                source_type="agent_team",
                source_id=str(team.id),
                entry_type="team_memory",
                title="Foreign workspace memory",
                content="Must not leak.",
                content_fingerprint=memory_content_fingerprint(
                    "Foreign workspace memory", "Must not leak."
                ),
                visibility_scope="team",
                importance=100,
            ),
        ]
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/project-space",
        headers=_headers(owner.id),
    )
    missing_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{uuid4()}/project-space",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["team"]["id"] == str(team.id)
    assert body["team"]["primary_runtime_space_id"] == str(runtime_space.id)
    assert body["summary"]["task_count"] == 1
    assert body["summary"]["active_run_count"] == 1
    assert body["summary"]["artifact_count"] == 1
    assert body["summary"]["workspace_file_count"] == 1
    assert body["summary"]["memory_entry_count"] == 2
    assert {space["id"] for space in body["runtime_spaces"]} == {
        str(runtime_space.id),
        str(task_runtime_space.id),
    }
    assert body["capacity"]["storage"]["total_bytes"] == 3072
    assert body["capacity"]["reservations"]["resource_usage"]["active_runs"] == 1
    assert body["max_project_space"]["effective_limits"]["active_runs"] == 10
    assert body["landing_rules"]["workspace_file_source"] == "workspace_file.metadata"
    developer_output = next(
        item for item in body["employee_outputs"] if item["agent_profile_id"] == str(developer.id)
    )
    assert developer_output["artifact_count"] == 1
    assert developer_output["artifact_bytes"] == 2048
    assert developer_output["memory_entry_count"] == 1
    assert body["project_tasks"][0]["task_id"] == str(task.id)
    assert body["project_tasks"][0]["artifact_bytes"] == 2048
    assert body["space_tiers"][1]["tier"] == "standard"
    body_text = json.dumps(body)
    assert str(foreign_task.id) not in body_text
    assert "Foreign team memory" not in body_text
    assert "Foreign workspace memory" not in body_text
    assert "sk-project-space-secret" not in body_text
    assert "file-hidden-token" not in body_text
    assert missing_response.status_code == 403


def test_team_runtime_timeline_includes_blocked_step_provider_metadata() -> None:
    _, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Blocked Provider",
        provider="openai-compatible",
        api_key="sk-step-provider-secret",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1/private",
        is_default=False,
    )
    credential.health_status = "unhealthy"
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    session.add(developer)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Blocked Step Timeline Team",
        team_type="software",
        manager_agent_profile_id=developer.id,
    )
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Build provider blocked feature",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        title="Use blocked provider",
        status="queued",
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "model_provider_unavailable",
            "blocked_details": {
                "error_type": "ValueError",
                "message": "Agent model provider credential not found or unavailable",
                "model_provider": {
                    "source": "agent_override",
                    "agent_profile_id": str(developer.id),
                    "agent_model": "workspace-default",
                    "credential_id": str(credential.id),
                    "credential_reference": f"model_provider_credentials:{credential.id}",
                    "credential_status": "active",
                    "credential_health_status": "unhealthy",
                    "budget_exhausted": False,
                    "api_key": "sk-step-provider-secret",
                    "base_url": "https://provider.example.test/v1/private",
                },
            },
        },
    )
    session.add(step)
    session.commit()
    timeline = TeamTimelineService(session).timeline(
        workspace_id=workspace.id,
        team_id=team.id,
        filters=TimelineFilters(
            source_type="task_step", event_type="team.runtime.step.scheduling_blocked"
        ),
    )
    assert timeline is not None
    assert timeline.summary.total_events == 1
    assert timeline.summary.source_counts == {"task_step": 1}
    assert timeline.summary.event_type_counts == {"team.runtime.step.scheduling_blocked": 1}
    item = timeline.items[0]
    assert item.message == "Team runtime step scheduling blocked: model_provider_unavailable"
    assert item.resource_id == str(step.id)
    assert item.metadata["task_id"] == str(task.id)
    assert item.metadata["task_step_id"] == str(step.id)
    assert item.metadata["assigned_agent_profile_id"] == str(developer.id)
    assert item.metadata["blocked_reason"] == "model_provider_unavailable"
    provider = item.metadata["blocked_details"]["model_provider"]
    assert provider["credential_reference"] == f"model_provider_credentials:{credential.id}"
    assert provider["credential_health_status"] == "unhealthy"
    assert provider["api_key"] == "[redacted]"
    assert provider["base_url"] == "[redacted]"
    assert "sk-step-provider-secret" not in str(timeline)
    assert "provider.example.test/v1/private" not in str(timeline)


def test_team_operations_console_aggregates_runtime_members_sessions_and_mailbox() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id, name="Operations Console Runtime", status="active"
    )
    session.add_all([manager, runtime])
    session.flush()
    default_credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Workspace OpenAI",
        provider="openai",
        api_key="sk-default-provider-secret",
        default_model="gpt-4.1-mini",
        base_url=None,
        is_default=True,
    )
    credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="OpenAI Gateway",
        provider="openai-compatible",
        api_key="sk-console-provider-secret",
        default_model="gpt-4.1",
        base_url="https://dash.ovload.com/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    session.add(developer)
    session.flush()
    session.add(
        WorkspaceScheduledJob(
            workspace_id=workspace.id,
            created_by_user_id=owner.id,
            name="OpenAI provider health",
            schedule_type="hourly",
            schedule_config={"minute": 15},
            status="active",
            action_type="temporal_execution",
            execution_kind=ExecutionKind.MODEL_PROVIDER_HEALTH.value,
            resource_id=credential.id,
            parameters={"probes": ["models"], "timeout_seconds": 5, "api_key": "sk-never-return"},
            metadata_={"token": "hidden"},
            next_run_at=datetime.now(UTC) + timedelta(minutes=30),
        )
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Operations Console Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
        coordination_rules={"operating_policy": {"cadence": "hourly", "manager_can_assign": True}},
        default_task_policy={
            "priority": 6,
            "team_runtime": {"status": "running", "workspace_runtime_id": str(runtime.id)},
            "team_runtime_thread_id": "internal-thread-control",
        },
    )
    other_team = AgentTeam(
        workspace_id=workspace.id, name="Other Operations Team", team_type="company"
    )
    session.add_all([team, other_team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="developer",
                order_index=2,
            ),
        ]
    )
    blocked_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        agent_team_id=team.id,
        title="Blocked provider rollout",
        priority=9,
        status=TaskStatus.QUEUED.value,
    )
    session.add(blocked_task)
    session.flush()
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        assigned_agent_profile_id=developer.id,
        title="Run with blocked provider",
        status="queued",
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "model_provider_unavailable",
            "blocked_details": {
                "model_provider": {
                    "credential_reference": f"model_provider_credentials:{credential.id}",
                    "credential_health_status": "unhealthy",
                    "api_key": "sk-blocked-step-secret",
                    "base_url": "https://blocked.example.test/v1/private",
                }
            },
        },
    )
    session.add(blocked_step)
    queued_run = AgentRun(
        workspace_id=workspace.id,
        task_id=blocked_task.id,
        task_step_id=blocked_step.id,
        agent_profile_id=developer.id,
        status=RunStatus.QUEUED.value,
        model="gpt-4.1",
        input={
            "authorization_snapshot": {
                "model_provider": {
                    "provider": "openai-compatible",
                    "credential_id": str(credential.id),
                    "credential_reference": f"model_provider_credentials:{credential.id}",
                    "model_api": "chat_completions",
                    "readiness_status": "degraded",
                    "reasons": [],
                    "warnings": ["model_provider_health_unknown"],
                    "api_key": "sk-run-provider-secret",
                    "base_url": "https://run-provider.example.test/private",
                }
            }
        },
    )
    session.add(queued_run)
    session.commit()
    session.add(
        WorkspaceMemoryEntry(
            workspace_id=workspace.id,
            scope_type="workspace",
            scope_id=str(workspace.id),
            created_by_user_id=owner.id,
            entry_type="operating_note",
            title="Console company policy",
            content="Company-level memory is visible in the operations console.",
            content_fingerprint=memory_content_fingerprint(
                "Console company policy",
                "Company-level memory is visible in the operations console.",
            ),
            tags=["company"],
            visibility_scope="company",
            importance=3,
        )
    )
    session.commit()
    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={
            "reason": "start operating console",
            "metadata": {
                "api_key": "sk-runtime-metadata",
                "nested": {"authorization": "Bearer runtime-hidden"},
            },
        },
    )
    assert started.status_code == 200
    thread_id = started.json()["thread_id"]
    team_session = session.scalar(
        select(AgentSessionMetadata).where(
            AgentSessionMetadata.workspace_id == workspace.id,
            AgentSessionMetadata.agent_team_id == team.id,
            AgentSessionMetadata.scope_type == "team_runtime",
        )
    )
    assert team_session is not None
    team_session.latest_item_metadata = {"token": "session-secret-token", "visible": "latest"}
    message = AgentMessage(
        workspace_id=workspace.id,
        thread_id=UUID(thread_id),
        agent_team_id=team.id,
        sender_agent_profile_id=manager.id,
        recipient_agent_profile_id=developer.id,
        message_type="handoff",
        body="Build the customer dashboard with token sk-console hidden",
        payload={
            "api_key": "sk-console",
            "nested": {"authorization": "Bearer mailbox-hidden"},
            "visible": "handoff",
        },
        status="sent",
    )
    other_thread = AgentMessageThread(
        workspace_id=workspace.id, agent_team_id=other_team.id, subject="Other team thread"
    )
    session.add_all([message, other_thread])
    session.flush()
    identity = ExecutionIdentityService(session).capture(workspace.id, owner.id)
    session.add_all(
        [
            TeamOrchestrationRequest(
                workspace_id=workspace.id,
                team_id=team.id,
                execution_identity=identity,
                status=status,
            )
            for status in ("queued", "failed", "unknown")
        ]
        + [
            TeamOrchestrationRequest(
                workspace_id=workspace.id,
                team_id=other_team.id,
                execution_identity=identity,
                status="queued",
            )
        ]
    )
    session.add(
        AgentMessage(
            workspace_id=workspace.id,
            thread_id=other_thread.id,
            agent_team_id=other_team.id,
            sender_agent_profile_id=manager.id,
            recipient_agent_profile_id=developer.id,
            message_type="handoff",
            body="This other team unread message must not affect the current console.",
            status="sent",
        )
    )
    session.commit()
    session.expire_all()
    raw_console = TeamOperationsConsoleService(session).get_console(
        workspace_id=workspace.id, team_id=team.id
    )
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )
    assert raw_console is not None
    raw_serialized = str(raw_console)
    assert "sk-runtime-metadata" not in raw_serialized
    assert "Bearer runtime-hidden" not in raw_serialized
    assert "session-secret-token" not in raw_serialized
    assert "sk-console" not in raw_serialized
    assert "Bearer mailbox-hidden" not in raw_serialized
    assert raw_console["mailbox"]["latest_messages"][0]["payload"] == {
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
        "visible": "handoff",
    }
    assert response.status_code == 200
    body = response.json()
    assert body["team"]["name"] == "Operations Console Team"
    assert body["team"]["coordination_rules"]["operating_policy"]["cadence"] == "hourly"
    assert body["team"]["default_task_policy"] == {"priority": 6}
    assert body["runtime"]["status"] == "running"
    assert body["runtime"]["workspace_runtime_id"] == str(runtime.id)
    assert body["runtime"]["runtime_health"] == "healthy"
    assert body["runtime"]["ready"] is True
    assert body["runtime"]["queue"] == {
        "available": True,
        "mode": "temporal",
        "queued": 2,
        "running": 0,
        "completed": 0,
        "failed": 1,
        "unknown": 1,
    }
    assert body["runtime"]["blocked_steps"]["count"] == 1
    assert body["runtime"]["blocked_steps"]["blocked_reasons"] == {"model_provider_unavailable": 1}
    assert body["runtime"]["blocked_steps"]["latest"][0]["task_id"] == str(blocked_task.id)
    assert body["runtime"]["blocked_steps"]["latest"][0]["task_step_id"] == str(blocked_step.id)
    assert body["runtime"]["blocked_steps"]["latest"][0]["blocked_details"]["model_provider"] == {
        "credential_reference": f"model_provider_credentials:{credential.id}",
        "credential_health_status": "unhealthy",
        "api_key": "[redacted]",
        "base_url": "[redacted]",
    }
    assert "sk-blocked-step-secret" not in str(body["runtime"]["blocked_steps"])
    assert "blocked.example.test/v1/private" not in str(body["runtime"]["blocked_steps"])
    assert (
        body["runtime"]["operating_policy"]["coordination_rules"]["operating_policy"]["cadence"]
        == "hourly"
    )
    assert body["runtime"]["memory_summary"]["scope_counts"] == {"company": 1}
    assert body["sessions"]["total"] == 3
    assert body["sessions"]["team_session"]["scope_type"] == "team_runtime"
    assert len(body["sessions"]["member_sessions"]) == 2
    assert {item["team_role"] for item in body["members"]} == {"project_manager", "developer"}
    developer_member = next(
        item for item in body["members"] if item["agent_profile_id"] == str(developer.id)
    )
    model_provider = developer_member["agent"]["model_provider"]
    assert developer_member["agent"]["model"] == "workspace-default"
    assert developer_member["agent"]["model_provider_credential_id"] == str(credential.id)
    assert model_provider["source"] == "agent_override"
    assert model_provider["credential_id"] == str(credential.id)
    assert model_provider["credential_reference"] == f"model_provider_credentials:{credential.id}"
    assert model_provider["credential_name"] == "OpenAI Gateway"
    assert model_provider["provider"] == "openai-compatible"
    assert model_provider["selected_model"] == "gpt-4.1"
    assert model_provider["agent_model"] == "workspace-default"
    assert model_provider["model_capability"] is None
    assert model_provider["default_model"] == "gpt-4.1"
    assert model_provider["model_api"] == "chat_completions"
    assert model_provider["base_url_configured"] is True
    assert model_provider["base_url_host"] == "dash.ovload.com"
    assert model_provider["api_key_fingerprint"] == credential.api_key_fingerprint
    assert model_provider["credential_status"] == "active"
    assert model_provider["credential_health_status"] == "unknown"
    assert model_provider["failure_count"] == 0
    assert model_provider["budget_exhausted"] is False
    assert model_provider["readiness_status"] == "degraded"
    assert model_provider["reasons"] == []
    assert model_provider["warnings"] == ["model_provider_unknown"]
    assert model_provider["last_health_check_at"] is None
    assert model_provider["last_success_at"] is None
    assert model_provider["last_failure_at"] is None
    assert model_provider["last_failure_code"] is None
    assert model_provider["last_failure_message"] is None
    assert model_provider["is_default"] is False
    assert model_provider["budget_metadata"] == {"model_api": "chat_completions"}
    assert model_provider["scheduled_health_check"]["configured"] is True
    assert model_provider["scheduled_health_check"]["active_count"] == 1
    assert model_provider["scheduled_health_check"]["paused_count"] == 0
    assert model_provider["scheduled_health_check"]["jobs"][0]["parameters"] == {
        "probes": ["models"],
        "timeout_seconds": 5,
    }
    provider_management = body["provider_management"]
    assert provider_management["credential_count"] == 2
    assert provider_management["active_credential_count"] == 2
    assert provider_management["default_credential_id"] == str(default_credential.id)
    run_diagnostics = provider_management["run_diagnostics"]
    assert run_diagnostics["total"] == 1
    assert run_diagnostics["items"][0]["run_id"] == str(queued_run.id)
    assert run_diagnostics["items"][0]["provider_snapshot_source"] == "frozen_run_snapshot"
    assert run_diagnostics["items"][0]["provider"] == "openai-compatible"
    assert run_diagnostics["items"][0]["model_api"] == "chat_completions"
    assert (
        run_diagnostics["items"][0]["credential_reference"]
        == f"model_provider_credentials:{credential.id}"
    )
    assert "sk-run-provider-secret" not in str(run_diagnostics)
    assert "run-provider.example.test/private" not in str(run_diagnostics)
    provider_options = {item["id"]: item for item in provider_management["credentials"]}
    assert set(provider_options) == {str(default_credential.id), str(credential.id)}
    assert provider_options[str(default_credential.id)]["provider"] == "openai"
    assert provider_options[str(default_credential.id)]["is_default"] is True
    assert provider_options[str(default_credential.id)]["selectable"] is True
    assert provider_options[str(default_credential.id)]["base_url_configured"] is False
    assert provider_options[str(default_credential.id)]["model_apis"] == [
        "responses",
        "chat_completions",
    ]
    assert provider_options[str(default_credential.id)]["default_model_api"] is None
    assert provider_options[str(default_credential.id)]["model_options"] == []
    assert provider_options[str(credential.id)]["provider"] == "openai-compatible"
    assert provider_options[str(credential.id)]["model_api"] == "chat_completions"
    assert provider_options[str(credential.id)]["model_apis"] == ["responses", "chat_completions"]
    assert provider_options[str(credential.id)]["default_model_api"] is None
    assert provider_options[str(credential.id)]["base_url_host"] == "dash.ovload.com"
    assert provider_options[str(credential.id)]["model_options"] == []
    assert provider_options[str(credential.id)]["scheduled_health_check"]["configured"] is True
    developer_binding = next(
        item
        for item in provider_management["agent_bindings"]
        if item["agent_profile_id"] == str(developer.id)
    )
    manager_binding = next(
        item
        for item in provider_management["agent_bindings"]
        if item["agent_profile_id"] == str(manager.id)
    )
    assert developer_binding["credential_id"] == str(credential.id)
    assert developer_binding["provider"] == "openai-compatible"
    assert developer_binding["default_model"] == "gpt-4.1"
    assert developer_binding["model_api"] == "chat_completions"
    assert developer_binding["model_apis"] == ["responses", "chat_completions"]
    assert developer_binding["default_model_api"] is None
    assert developer_binding["credential_status"] == "active"
    assert developer_binding["credential_health_status"] == "unknown"
    assert developer_binding["budget_exhausted"] is False
    assert developer_binding["model_capability"] is None
    assert developer_binding["available_credential_ids"] == [
        str(default_credential.id),
        str(credential.id),
    ]
    assert manager_binding["credential_id"] == str(default_credential.id)
    assert manager_binding["source"] == "workspace_default"
    provider_management_actions = provider_management["suggested_actions"]
    assert provider_management_actions[0]["source"] == "provider_management"
    assert provider_management_actions[0]["action"] == "review_model_provider"
    assert provider_management_actions[0]["reason"] == "agent_model_provider_degraded"
    developer_provider_management_action = next(
        item for item in provider_management_actions if item["credential_id"] == str(credential.id)
    )
    assert developer_provider_management_action["model_api"] == "chat_completions"
    assert developer_member["mailbox"]["unread_count"] == 1
    assert body["mailbox"]["thread_id"] == thread_id
    assert body["mailbox"]["unread_count"] == 2
    assert body["mailbox"]["latest_messages"][0]["task_id"] is None
    assert body["mailbox"]["latest_messages"][0]["agent_team_id"] == str(team.id)
    assert body["mailbox"]["latest_messages"][0]["body_preview"] == "[redacted]"
    assert body["mailbox"]["latest_messages"][0]["payload"] == {
        "api_key": "[redacted]",
        "nested": {"authorization": "[redacted]"},
        "visible": "handoff",
    }
    assert "sk-console" not in str(body)
    assert "sk-runtime-metadata" not in str(body)
    assert "Bearer runtime-hidden" not in str(body)
    assert "session-secret-token" not in str(body)
    assert "Bearer mailbox-hidden" not in str(body)
    assert "hidden-scheduler-token" not in str(body)
    assert "sk-never-return" not in str(body)
    assert "sk-queue-secret" not in str(body)
    assert "sk-retry-secret" not in str(body)
    assert "dead.example.test/private" not in str(body)
    assert "dead-token" not in str(body)
    assert "hidden" not in str(body)
    assert "dash.ovload.com/v1" not in str(body)
    assert body["controls"]["can_pause"] is True
    assert body["controls"]["can_ensure_workspace_runtime"] is False
    suggested_sources = {item["source"] for item in body["controls"]["suggested_actions"]}
    assert "provider_readiness" in suggested_sources
    provider_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["source"] == "provider_readiness"
    ]
    provider_management_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["source"] == "provider_management"
    ]
    assert provider_suggestions[0]["action"] == "review_model_provider"
    assert provider_suggestions[0]["reason"] == "team_model_provider_degraded"
    developer_provider_suggestion = next(
        item for item in provider_suggestions if item["credential_id"] == str(credential.id)
    )
    assert developer_provider_suggestion["model_api"] == "chat_completions"
    assert developer_provider_suggestion["model_apis"] == ["responses", "chat_completions"]
    assert developer_provider_suggestion["default_model_api"] is None
    assert provider_management_suggestions[0]["reason"] == "agent_model_provider_degraded"
    developer_provider_management_suggestion = next(
        item
        for item in provider_management_suggestions
        if item["credential_id"] == str(credential.id)
    )
    assert developer_provider_management_suggestion["model_api"] == "chat_completions"
    orchestration_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["source"] == "team_orchestration"
    ]
    assert len(orchestration_suggestions) == 1
    assert orchestration_suggestions[0]["reason"] == "team_request_outcome_unknown"
    assert orchestration_suggestions[0]["action"] == "reconcile_team_request"
    assert orchestration_suggestions[0]["count"] == 1
    blocked_step_suggestions = [
        item
        for item in body["controls"]["suggested_actions"]
        if item["reason"] == "team_runtime_step_scheduling_blocked"
    ]
    assert len(blocked_step_suggestions) == 1
    blocked_step_suggestion = blocked_step_suggestions[0]
    assert blocked_step_suggestion["source"] == "team_runtime"
    assert blocked_step_suggestion["automation"] == "team_runtime_control"
    assert blocked_step_suggestion["action"] == "review_scheduling_blocks"
    assert blocked_step_suggestion["priority"] == 90
    assert blocked_step_suggestion["count"] == 1
    assert blocked_step_suggestion["blocked_reasons"] == {"model_provider_unavailable": 1}
    assert blocked_step_suggestion["task_ids"] == [str(blocked_task.id)]
    assert blocked_step_suggestion["task_step_ids"] == [str(blocked_step.id)]
    assert body["command_center"]["summary"]["runtime_ready"] is True
    assert body["command_center"]["runtime"]["ready"] is True
    assert body["command_center"]["provider_readiness"]["status"] == "degraded"
    assert body["command_center"]["provider_readiness"]["member_count"] == 2
    assert body["command_center"]["provider_readiness"]["warning_reasons"] == {
        "model_provider_health_check_not_scheduled": 1,
        "model_provider_unknown": 2,
    }
    assert body["command_center"]["overview"]["team"]["name"] == "Operations Console Team"
    assert body["command_center"]["queues"]["handoff"]["total"] == 0
    assert body["command_center"]["queues"]["manager"]["total"] == 1
    assert body["command_center"]["operating_policy"]["default_task_policy"] == {"priority": 6}
    assert body["command_center"]["memory_summary"]["team_id"] == str(team.id)
    assert body["command_center"]["memory_summary"]["scope_counts"] == {"company": 1}
    assert body["command_center"]["runtime"]["memory_summary"]["scope_counts"] == {"company": 1}
    for policy in (
        body["team"]["default_task_policy"],
        body["runtime"]["operating_policy"]["default_task_policy"],
        body["command_center"]["operating_policy"]["default_task_policy"],
        body["command_center"]["runtime"]["operating_policy"]["default_task_policy"],
    ):
        assert "team_runtime" not in policy
        assert "team_runtime_thread_id" not in policy


def test_team_operations_console_read_does_not_initialize_runtime_state() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Read Only Console Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["runtime"]["thread_id"] is None
    assert body["runtime"]["team_session_id"] is None
    assert body["runtime"]["team_session_key"] is None
    assert body["runtime"]["member_session_count"] == 0
    assert body["sessions"]["total"] == 0
    assert body["mailbox"]["thread_id"] is None
    assert body["mailbox"]["unread_count"] == 0
    assert body["mailbox"]["latest_messages"] == []
    session.expire_all()
    assert (
        session.scalar(
            select(func.count(AgentSessionMetadata.id)).where(
                AgentSessionMetadata.workspace_id == workspace.id,
                AgentSessionMetadata.agent_team_id == team.id,
            )
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count(AgentMessageThread.id)).where(
                AgentMessageThread.workspace_id == workspace.id
            )
        )
        == 0
    )


def test_team_operations_console_marks_inactive_provider_not_selectable() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(
        workspace_id=workspace.id, name="PM", role="project_manager", model="workspace-default"
    )
    session.add(manager)
    session.flush()
    credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Inactive OpenAI Gateway",
        provider="openai-compatible",
        api_key="sk-inactive-console",
        default_model="gpt-4.1-mini",
        base_url="https://inactive-console.example.test/v1",
        is_default=False,
    )
    credential.status = "inactive"
    manager.model_provider_credential_id = credential.id
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Inactive Provider Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    option = body["provider_management"]["credentials"][0]
    binding = body["provider_management"]["agent_bindings"][0]
    assert option["id"] == str(credential.id)
    assert option["selectable"] is False
    assert option["not_selectable_reasons"] == ["model_provider_not_active"]
    assert binding["credential_status"] == "inactive"
    assert binding["readiness_status"] == "blocked"
    assert binding["reasons"] == ["model_provider_not_active"]
    assert binding["available_credential_ids"] == []
    assert body["command_center"]["provider_readiness"]["status"] == "blocked"
    readiness_member = body["command_center"]["provider_readiness"]["members"][0]
    assert readiness_member["credential_reference"] == f"model_provider_credentials:{credential.id}"
    assert body["command_center"]["provider_readiness"]["runtime_blocking_reasons"] == {
        "model_provider_not_active": 1
    }
    provider_action = body["command_center"]["action_plan"][0]
    assert provider_action["source"] == "provider_readiness"
    assert provider_action["credential_reference"] == f"model_provider_credentials:{credential.id}"
    assert "sk-inactive-console" not in str(body)
    assert "inactive-console.example.test/v1" not in str(body)


def test_team_operations_console_provider_readiness_blocks_unhealthy_default() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    service = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    )
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Primary Default",
        provider="openai",
        api_key="sk-primary-readiness",
        default_model="primary-model",
        base_url=None,
        is_default=True,
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Backup OpenAI Gateway",
        provider="openai-compatible",
        api_key="sk-backup-readiness",
        default_model="backup-model",
        base_url="https://backup-readiness.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
    )
    primary.health_status = "unhealthy"
    primary.failure_count = 3
    primary.last_failure_at = datetime.now(UTC)
    primary.last_failure_code = "InternalServerError"
    primary.last_failure_message = "Provider failed with sk-primary-readiness"
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="workspace-default",
        model_settings={"model_api": "responses"},
    )
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Fallback Readiness Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    binding = body["provider_management"]["agent_bindings"][0]
    readiness = body["command_center"]["provider_readiness"]
    readiness_member = readiness["members"][0]
    provider_action = body["command_center"]["action_plan"][0]
    assert binding["source"] == "agent_model"
    assert binding["credential_id"] is None
    assert binding["credential_reference"] is None
    assert binding["selected_model"] == "workspace-default"
    assert binding["provider"] is None
    assert binding["model_api"] is None
    assert binding["readiness_status"] == "blocked"
    assert binding["reasons"] == ["model_provider_unavailable"]
    assert readiness["status"] == "blocked"
    assert readiness["runtime_blocked_member_count"] == 1
    assert readiness["runtime_degraded_member_count"] == 0
    assert readiness_member["credential_id"] == str(primary.id)
    assert readiness_member["credential_reference"] == f"model_provider_credentials:{primary.id}"
    assert readiness_member["provider"] == "openai"
    assert readiness_member["model"] == "primary-model"
    assert readiness_member["model_api"] == "responses"
    assert readiness_member["readiness_status"] == "blocked"
    assert readiness_member["reasons"] == binding["reasons"] == ["model_provider_unavailable"]
    assert readiness_member["failure_count"] == 3
    assert readiness_member["last_failure_code"] == "InternalServerError"
    assert readiness_member["last_failure_message"] == "[redacted]"
    assert provider_action["source"] == "provider_readiness"
    assert provider_action["model_api"] == "responses"
    assert provider_action["failure_count"] == 3
    assert provider_action["last_failure_code"] == "InternalServerError"
    assert provider_action["last_failure_message"] == "[redacted]"
    assert backup.status == "active"
    assert "sk-primary-readiness" not in str(body)
    assert "sk-backup-readiness" not in str(body)
    assert "backup-readiness.example.test/v1" not in str(body)


def test_team_operations_console_blocks_missing_explicit_provider() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    default_credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Workspace Default",
        provider="openai",
        api_key="sk-default-console",
        default_model="gpt-4.1-mini",
        base_url=None,
        is_default=True,
    )
    foreign_owner, foreign_workspace = _seed_workspace(
        session, role="owner", email="foreign-console@example.com", slug="foreign-console"
    )
    foreign_credential = ModelProviderCredentialCommandService(
        session, SecretEncryptionService(secret="unit-test-secret", key_id="test-key")
    ).create(
        workspace_id=foreign_workspace.id,
        created_by_user_id=foreign_owner.id,
        name="Foreign credential",
        provider="openai",
        api_key="sk-foreign-console",
        default_model="gpt-4.1-mini",
        base_url=None,
        is_default=False,
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="PM",
        role="project_manager",
        model="workspace-default",
        model_provider_credential_id=foreign_credential.id,
    )
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Missing Provider Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
        )
    )
    session.commit()
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )
    assert response.status_code == 200
    body = response.json()
    binding = body["provider_management"]["agent_bindings"][0]
    assert binding["source"] == "unavailable"
    assert binding["credential_id"] == str(manager.model_provider_credential_id)
    assert binding["readiness_status"] == "blocked"
    assert binding["reasons"] == ["model_provider_unavailable"]
    assert binding["available_credential_ids"] == [str(default_credential.id)]
    readiness = body["command_center"]["provider_readiness"]
    assert readiness["status"] == "blocked"
    assert readiness["runtime_blocking_reasons"] == {"model_provider_unavailable": 1}
    assert readiness["members"][0]["credential_id"] == str(manager.model_provider_credential_id)
    assert (
        readiness["members"][0]["credential_reference"]
        == f"model_provider_credentials:{manager.model_provider_credential_id}"
    )
    assert readiness["members"][0]["reasons"] == ["model_provider_unavailable"]
    provider_action = body["command_center"]["action_plan"][0]
    assert provider_action["source"] == "provider_readiness"
    assert provider_action["credential_id"] == str(manager.model_provider_credential_id)
    assert (
        provider_action["credential_reference"]
        == f"model_provider_credentials:{manager.model_provider_credential_id}"
    )
    assert "sk-default-console" not in str(body)


def test_team_runtime_ignores_foreign_team_thread_reference() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    other_manager = AgentProfile(workspace_id=workspace.id, name="Other PM", role="project_manager")
    session.add_all([manager, other_manager])
    session.flush()
    other_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Runtime Team",
        team_type="company",
        manager_agent_profile_id=other_manager.id,
    )
    session.add(other_team)
    session.flush()
    other_thread = AgentMessageThread(
        workspace_id=workspace.id, agent_team_id=other_team.id, subject="Other team runtime"
    )
    session.add(other_thread)
    session.flush()
    session.add(
        AgentMessage(
            workspace_id=workspace.id,
            thread_id=other_thread.id,
            agent_team_id=other_team.id,
            sender_agent_profile_id=other_manager.id,
            recipient_agent_profile_id=other_manager.id,
            message_type="team.runtime.started",
            body="Other team runtime message",
            status="sent",
        )
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Scoped Runtime Team",
        team_type="company",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {"status": "running"},
            "team_runtime_thread_id": str(other_thread.id),
        },
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()
    runtime = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime", headers=_headers(owner.id)
    )
    console = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/operations-console",
        headers=_headers(owner.id),
    )
    started = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/start",
        headers=_headers(owner.id),
        json={"reason": "create scoped runtime thread"},
    )
    assert runtime.status_code == 200
    assert runtime.json()["thread_id"] is None
    assert runtime.json()["last_message_at"] is None
    assert console.status_code == 200
    assert console.json()["mailbox"]["thread_id"] is None
    assert console.json()["mailbox"]["latest_messages"] == []
    assert "Other team runtime message" not in str(console.json())
    assert started.status_code == 200
    assert started.json()["thread_id"] != str(other_thread.id)
    session.expire_all()
    stored_team = session.get(AgentTeam, team.id)
    assert stored_team is not None
    created_thread_id = UUID(stored_team.default_task_policy["team_runtime_thread_id"])
    assert created_thread_id != other_thread.id
    created_thread = session.get(AgentMessageThread, created_thread_id)
    assert created_thread is not None
    assert created_thread.agent_team_id == team.id


@pytest.mark.skipif(os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Real Temporal required")
def test_team_runtime_state_recovers_last_advancement_and_continue_context() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
    template = RuntimeTemplate(
        name="recovery-runtime-template",
        image="python@sha256:" + "0" * 64,
        created_at=datetime.now(UTC),
    )
    session.add_all([manager, template])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Recovery Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={
            "team_runtime": {"status": "running", "runtime_template_id": str(template.id)}
        },
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=manager.id,
            team_role="project_manager",
            order_index=1,
        )
    )
    session.commit()
    ran = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/orchestration/requests",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "apply_command_center_actions": False,
            "finalize_ready_tasks": False,
            "enqueue_runs": False,
        },
    )
    assert ran.status_code == 202
    result = run_team_request(session, UUID(ran.json()["request_id"]))
    stopped = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/stop",
        headers=_headers(owner.id),
        json={"reason": "simulate restart"},
    )
    continued = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime/continue",
        headers=_headers(owner.id),
        json={"instruction": "restore from persisted runtime state"},
    )
    recovered = client.get(
        f"/api/v1/workspaces/{workspace.id}/teams/{team.id}/runtime", headers=_headers(owner.id)
    )
    assert result["status"] == "noop"
    assert stopped.status_code == 200
    assert stopped.json()["runtime_health"] == "stopped"
    assert continued.status_code == 200
    assert continued.json()["status"] == "running"
    assert continued.json()["last_advancement"]["advancement"] == 1
    assert continued.json()["runtime_health"] == "healthy"
    assert recovered.status_code == 200
    recovered_body = recovered.json()
    assert recovered_body["last_advancement"]["status"] == "noop"
    assert recovered_body["last_advancement"]["summary"]["finalize_ready_tasks"] is False
    assert recovered_body["last_message_at"] is not None
    assert recovered_body["runtime_health"] == "healthy"
    session.expire_all()
    stored_team = session.get(AgentTeam, team.id)
    assert stored_team is not None
    runtime_metadata = stored_team.default_task_policy["team_runtime"]
    assert runtime_metadata["advancement_count"] == 1
    assert runtime_metadata["continued_from_advancement"]["advancement"] == 1
    assert _pending_dispatch_count(session, workspace.id) >= 1
