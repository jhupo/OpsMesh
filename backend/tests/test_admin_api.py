from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
from fastapi.testclient import TestClient
from pytest import raises
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.capabilities.catalog.models import Capability, ToolGroup
from backend.app.capabilities.marketplace.models import MarketplaceListing
from backend.app.capabilities.mcp.models import McpServer, McpToolAllowlist
from backend.app.capabilities.plugins.contracts import PluginAction
from backend.app.capabilities.plugins.models import (
    PluginCredential,
    PluginDeployment,
    PluginInstall,
    PluginRelease,
    PluginTrustKey,
)
from backend.app.capabilities.plugins.service import PluginService
from backend.app.capabilities.plugins.services import PluginPrincipal, PluginServices
from backend.app.capabilities.references.models import CapabilityResource
from backend.app.capabilities.skills.models import Skill, WorkspaceSkillInstall
from backend.app.governance.audit.models import AuditEvent
from backend.app.governance.policies.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.governance.security_events.models import SecurityEvent
from backend.app.identity.auth.models import UserAPIToken
from backend.app.identity.authorization.resources import ResourceAccessDenied
from backend.app.identity.users.models import User
from backend.app.main import create_app
from backend.app.orchestration.approvals.models import Approval
from backend.app.orchestration.runs.models import AgentRun
from backend.app.orchestration.tasks.models import Task
from backend.app.runtime.instances.models import RuntimeEvent, RuntimeLease, WorkspaceRuntime
from backend.app.runtime.queues.contracts import JobPayload, JobType
from backend.app.runtime.queues.dependencies import get_worker_queue
from backend.app.runtime.queues.service import RedisQueue
from backend.app.runtime.spaces.models import RuntimeSpace, RuntimeSpaceEvent, RuntimeSpaceQuota
from backend.app.runtime.workers.models import WorkerLease, WorkerNode
from backend.app.shared.config import Settings, get_settings
from backend.app.shared.db.base import Base
from backend.app.shared.db.session import get_db_session
from backend.app.shared.errors import PolicyDeniedError
from backend.app.shared.redis.dependencies import get_redis_client
from backend.app.shared.redis.keys import RedisKeyBuilder
from backend.app.workspaces.management.models import Workspace
from backend.app.workspaces.members.models import WorkspaceMember
from backend.app.workspaces.projects.models import WorkspaceProject, WorkspaceProjectQuota
from backend.app.workspaces.quotas.models import WorkspaceQuota
from backend.app.workspaces.quotas.reservations import (
    WorkspaceQuotaService as WorkspaceQuotaReservationService,
)

TOKEN = "test-token"
ADMIN_TOKEN = "admin-token"


def test_admin_api_requires_platform_admin_token() -> None:
    client, session, _ = _client()
    _seed_workspace(session)

    missing = client.get("/api/v1/admin/overview")
    wrong = client.get("/api/v1/admin/overview", headers={"Authorization": "Bearer wrong"})
    accepted = client.get("/api/v1/admin/overview", headers=_admin_headers())

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    rejected_events = session.query(SecurityEvent).filter_by(
        action="auth.platform_admin.rejected",
    )
    assert rejected_events.count() == 2


def test_every_admin_endpoint_rejects_an_authenticated_workspace_owner() -> None:
    import re

    from backend.app.identity.auth.service import AuthenticationService

    client, session, _ = _client()
    user, _ = _seed_workspace(session)
    created = AuthenticationService(session).create_user_api_token(
        user_id=user.id,
        name="admin-boundary-regression",
        settings=client.app.state.settings,
    )
    paths = client.app.openapi()["paths"]
    checked = []
    for template, operations in paths.items():
        if not template.startswith("/api/v1/admin/"):
            continue
        path = re.sub(r"\{[^}]+\}", str(uuid4()), template)
        for method in operations:
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            response = client.request(
                method.upper(),
                path,
                headers={"Authorization": f"Bearer {created.token}"},
            )
            assert response.status_code == 401, (method, template, response.text)
            checked.append((method, template))
    assert checked
    rejected = session.query(SecurityEvent).filter_by(action="auth.platform_admin.rejected")
    assert rejected.count() == len(checked)


def test_admin_api_exposes_global_control_plane_metadata() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace(
        session,
        email="other@example.com",
        slug="other",
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="workspace",
        status="active",
        policy={},
        network_policy={"mode": "none"},
        storage_policy={},
        cleanup_policy={},
    )
    worker = WorkerNode(
        worker_id="worker-1",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        worker_version="2026.05.19",
        hostname="host-a",
        capacity={"max_jobs": 2},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id="worker-1",
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=uuid4(),
        status="running",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC),
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="team-runtime",
        docker_container_id="container-admin-visible",
    )
    session.add(runtime)
    session.flush()
    runtime_lease = RuntimeLease(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        runtime_space_id=runtime_space.id,
        docker_container_id="container-admin-visible",
        status="running",
        lease_metadata={"scope": "admin"},
        acquired_at=datetime.now(UTC),
    )
    security_event = SecurityEvent(
        workspace_id=other_workspace.id,
        user_id=None,
        action="runtime.policy.violation",
        outcome="denied",
        severity="critical",
        path="/api/v1/workspaces/x/runtimes",
        method="POST",
        reason="bad runtime",
        event_metadata={},
        created_at=datetime.now(UTC),
    )
    session.add_all([runtime_space, worker, lease, runtime_lease, security_event])
    session.commit()

    overview = client.get("/api/v1/admin/overview", headers=_admin_headers())
    workers = client.get("/api/v1/admin/workers", headers=_admin_headers())
    leases = client.get("/api/v1/admin/worker-leases", headers=_admin_headers())
    runtime_leases = client.get(
        f"/api/v1/admin/runtime-leases?workspace_id={workspace.id}",
        headers=_admin_headers(),
    )
    spaces = client.get("/api/v1/admin/runtime-spaces", headers=_admin_headers())
    events = client.get("/api/v1/admin/security-events?severity=critical", headers=_admin_headers())
    workspaces = client.get("/api/v1/admin/workspaces", headers=_admin_headers())

    assert overview.status_code == 200
    assert overview.json()["workspaces_total"] == 2
    assert overview.json()["workers_online"] == 1
    assert overview.json()["active_worker_leases"] == 1
    assert overview.json()["critical_security_events"] == 1
    assert workers.status_code == 200
    assert workers.json()["items"][0]["worker_id"] == "worker-1"
    assert leases.status_code == 200
    assert leases.json()["items"][0]["workspace_id"] == str(workspace.id)
    assert runtime_leases.status_code == 200
    assert runtime_leases.json()["total"] == 1
    assert runtime_leases.json()["items"][0]["workspace_runtime_id"] == str(runtime.id)
    assert runtime_leases.json()["items"][0]["docker_container_id"] == "container-admin-visible"
    assert runtime_leases.json()["items"][0]["has_docker_container"] is True
    assert spaces.status_code == 200
    assert spaces.json()["items"][0]["id"] == str(runtime_space.id)
    assert events.status_code == 200
    assert events.json()["total"] == 1
    assert workspaces.status_code == 200
    assert workspaces.json()["total"] == 2


def test_admin_can_manage_workspace_members_projects_and_system_logs() -> None:
    client, session, _ = _client()
    owner, workspace = _seed_workspace(session)
    invited, _ = _seed_workspace(session, email="member@example.com", slug="member-space")
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=invited.id,
        role="viewer",
        status="active",
    )
    project = WorkspaceProject(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Control Plane",
        slug="control-plane",
        description="Platform project",
        input_path="inputs",
        work_path="work",
        output_path="outputs",
        configuration={},
        configuration_version=1,
        status="active",
    )
    capability = Capability(
        key="runtime.shell",
        name="Runtime Shell",
        category="runtime",
        default_policy={},
        status="active",
    )
    tool_group = ToolGroup(
        key="runtime-tools",
        name="Runtime Tools",
        tool_names=["runtime.shell"],
        status="active",
    )
    session.add_all([member, project, capability, tool_group])
    session.commit()

    detail = client.get(
        f"/api/v1/admin/workspaces/{workspace.id}",
        headers=_admin_headers(),
    )
    members = client.get(
        f"/api/v1/admin/workspaces/{workspace.id}/members",
        headers=_admin_headers(),
    )
    assert detail.status_code == 200
    assert detail.json()["member_count"] == 2
    assert detail.json()["project_count"] == 1
    assert members.status_code == 200
    member_id = next(
        item["id"] for item in members.json()["items"] if item["user_id"] == str(invited.id)
    )

    updated_member = client.patch(
        f"/api/v1/admin/workspaces/{workspace.id}/members/{member_id}",
        headers=_admin_headers(),
        json={"role": "operator"},
    )
    projects = client.get(
        f"/api/v1/admin/workspaces/{workspace.id}/projects",
        headers=_admin_headers(),
    )
    catalog = client.get(
        f"/api/v1/admin/catalog/project?workspace_id={workspace.id}",
        headers=_admin_headers(),
    )
    catalog_detail = client.get(
        f"/api/v1/admin/catalog/project/{project.id}?workspace_id={workspace.id}",
        headers=_admin_headers(),
    )
    catalog_cross_workspace = client.get(
        f"/api/v1/admin/catalog/project/{project.id}?workspace_id={invited.id}",
        headers=_admin_headers(),
    )
    capabilities = client.get(
        "/api/v1/admin/catalog/capability",
        headers=_admin_headers(),
    )
    capability_detail = client.get(
        f"/api/v1/admin/catalog/capability/{capability.id}",
        headers=_admin_headers(),
    )
    tools = client.get(
        "/api/v1/admin/catalog/tool",
        headers=_admin_headers(),
    )
    archived = client.patch(
        f"/api/v1/admin/workspaces/{workspace.id}/projects/{project.id}/status",
        headers=_admin_headers(),
        json={"status": "archived", "reason": "Platform archive"},
    )
    logs = client.get(
        "/api/v1/admin/system/logs"
        f"?workspace_id={workspace.id}&action=platform.workspace.project_status_updated",
        headers=_admin_headers(),
    )

    assert updated_member.status_code == 200
    assert updated_member.json()["role"] == "operator"
    assert projects.status_code == 200
    assert projects.json()["items"][0]["id"] == str(project.id)
    assert catalog.status_code == 200
    assert catalog.json()["total"] == 1
    assert catalog.json()["items"][0]["workspace_id"] == str(workspace.id)
    assert catalog_detail.status_code == 200
    assert catalog_detail.json()["resource_id"] == str(project.id)
    assert catalog_cross_workspace.status_code == 404
    assert capabilities.status_code == 200
    assert capabilities.json()["items"][0]["resource_id"] == str(capability.id)
    assert capability_detail.status_code == 200
    assert capability_detail.json()["workspace_id"] is None
    assert tools.status_code == 200
    assert tools.json()["items"][0]["resource_id"] == str(tool_group.id)
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert logs.status_code == 200
    assert logs.json()["total"] == 1
    assert logs.json()["items"][0]["actor_id"] == "platform_admin"


def test_admin_can_manage_project_quotas_and_resource_authorization() -> None:
    client, session, _ = _client()
    owner, workspace = _seed_workspace(session)
    member_user, _ = _seed_workspace(session, email="project-member@example.com", slug="member")
    foreign_user, foreign_workspace = _seed_workspace(
        session,
        email="foreign@example.com",
        slug="foreign",
    )
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=member_user.id,
            role="viewer",
            status="active",
        )
    )
    project = WorkspaceProject(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Quota Project",
        slug="quota-project",
        description="Project with a bounded run budget",
        input_path="inputs",
        work_path="work",
        output_path="outputs",
        configuration={},
        configuration_version=1,
        status="active",
    )
    foreign_project = WorkspaceProject(
        workspace_id=foreign_workspace.id,
        created_by_user_id=foreign_user.id,
        name="Foreign Project",
        slug="foreign-project",
        description="Foreign project",
        input_path="inputs",
        work_path="work",
        output_path="outputs",
        configuration={},
        configuration_version=1,
        status="active",
    )
    session.add_all([project, foreign_project])
    session.commit()

    configured = client.put(
        f"/api/v1/admin/workspaces/{workspace.id}/projects/{project.id}/quotas",
        headers=_admin_headers(),
        json={"quotas": [{"quota_key": "active_runs", "limit_value": 1}]},
    )
    listed = client.get(
        f"/api/v1/admin/workspaces/{workspace.id}/projects/{project.id}/quotas",
        headers=_admin_headers(),
    )
    foreign_quota = client.put(
        f"/api/v1/admin/workspaces/{workspace.id}/projects/{foreign_project.id}/quotas",
        headers=_admin_headers(),
        json={"quotas": [{"quota_key": "active_runs", "limit_value": 1}]},
    )
    assert configured.status_code == 200
    assert configured.json()[0]["project_id"] == str(project.id)
    assert configured.json()[0]["limit_value"] == 1
    assert listed.status_code == 200
    assert listed.json()[0]["quota_key"] == "active_runs"
    assert foreign_quota.status_code == 404
    configured_quota = session.query(WorkspaceProjectQuota).filter_by(project_id=project.id).one()
    assert configured_quota.reserved_value == 0
    assert configured_quota.limit_value == 1
    assert configured_quota.status == "active"

    first = WorkspaceQuotaReservationService(session).reserve(
        workspace_id=workspace.id,
        project_id=project.id,
        task_id=None,
        task_step_id=None,
        reservation_key="admin-project-quota-1",
        resource_usage={"active_runs": 1},
    )
    second = WorkspaceQuotaReservationService(session).reserve(
        workspace_id=workspace.id,
        project_id=project.id,
        task_id=None,
        task_step_id=None,
        reservation_key="admin-project-quota-2",
        resource_usage={"active_runs": 1},
    )
    assert first.reservation is not None
    assert second.reservation is None
    assert second.blocked_reason == "workspace_project_quota_exceeded:active_runs"
    WorkspaceQuotaReservationService(session).release_reservation(first.reservation)

    initial_authorization = client.get(
        f"/api/v1/admin/workspaces/{workspace.id}/resources/project/{project.id}/authorization",
        headers=_admin_headers(),
    )
    owner_response = client.put(
        f"/api/v1/admin/workspaces/{workspace.id}/resources/project/{project.id}/owner",
        headers=_admin_headers(),
        json={"user_id": str(member_user.id)},
    )
    grants_response = client.put(
        f"/api/v1/admin/workspaces/{workspace.id}/resources/project/{project.id}/grants",
        headers=_admin_headers(),
        json={"user_id": str(member_user.id), "actions": ["read", "update"]},
    )
    authorization = client.get(
        f"/api/v1/admin/workspaces/{workspace.id}/resources/project/{project.id}/authorization",
        headers=_admin_headers(),
    )
    foreign_grant = client.put(
        f"/api/v1/admin/workspaces/{workspace.id}/resources/project/{project.id}/grants",
        headers=_admin_headers(),
        json={"user_id": str(foreign_user.id), "actions": ["read"]},
    )

    assert owner_response.status_code == 200
    assert initial_authorization.status_code == 200
    assert initial_authorization.json()["owner_user_id"] is None
    assert owner_response.json()["owner_user_id"] == str(member_user.id)
    assert grants_response.status_code == 200
    assert authorization.status_code == 200
    assert authorization.json()["grants"] == [
        {"user_id": str(member_user.id), "actions": ["read", "update"]}
    ]
    assert foreign_grant.status_code == 409
    actions = {
        event.action
        for event in session.query(AuditEvent).filter_by(workspace_id=workspace.id).all()
    }
    assert "platform.workspace.project_quotas_upserted" in actions
    assert "platform.resource.owner_assigned" in actions
    assert "platform.resource.grants_replaced" in actions


def test_admin_plugin_governance_blocks_execution_and_tenant_reenable() -> None:
    client, session, _ = _client()
    owner, workspace = _seed_workspace(session)
    _, foreign_workspace = _seed_workspace(
        session, email="foreign-plugin@example.com", slug="foreign-plugin"
    )
    trust_key = PluginTrustKey(
        workspace_id=workspace.id,
        key_id="publisher-a",
        plugin_key="incident-channel",
        public_key="a" * 64,
    )
    install = PluginInstall(
        workspace_id=workspace.id,
        plugin_key="incident-channel",
        current_version="1.0.0",
        status="active",
        generation=1,
    )
    session.add_all([trust_key, install])
    session.flush()
    release = PluginRelease(
        workspace_id=workspace.id,
        install_id=install.id,
        trust_key_id=trust_key.id,
        version="1.0.0",
        package={},
        checksum="a" * 64,
        approved_permissions=["messages.receive"],
    )
    credential = PluginCredential(
        workspace_id=workspace.id,
        install_id=install.id,
        generation=1,
        token_hash="b" * 64,
        permissions=["messages.receive"],
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    deployment = PluginDeployment(
        workspace_id=workspace.id,
        install_id=install.id,
        template_id=uuid4(),
        image="example.test/plugin@sha256:" + "c" * 64,
        desired_state="running",
        status="running",
        revision=1,
        applied_revision=1,
        generation=1,
        execution_identity={},
        configuration={},
        encrypted_environment="encrypted",
        encryption_key_id="test",
        next_check_at=datetime.now(UTC),
    )
    session.add_all([release, credential, deployment])
    session.commit()

    listed = client.get(
        f"/api/v1/admin/catalog/plugin_trust_key?workspace_id={workspace.id}",
        headers=_admin_headers(),
    )
    releases = client.get(
        f"/api/v1/admin/catalog/plugin_release?workspace_id={workspace.id}",
        headers=_admin_headers(),
    )
    unauthorized = client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/plugins/{install.id}/disable",
        json={"reason": "Security review"},
    )
    foreign = client.post(
        f"/api/v1/admin/workspaces/{foreign_workspace.id}/plugins/{install.id}/disable",
        headers=_admin_headers(),
        json={"reason": "Security review"},
    )
    disabled = client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/plugins/{install.id}/disable",
        headers=_admin_headers(),
        json={"reason": "Security review"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert "public_key" not in str(listed.json())
    assert releases.status_code == 200
    assert releases.json()["items"][0]["metadata"]["trust_key_id"] == str(trust_key.id)
    assert "package" not in str(releases.json())
    assert unauthorized.status_code == 401
    assert foreign.status_code == 404
    assert disabled.status_code == 200
    assert disabled.json()["platform_blocked"] is True
    assert disabled.json()["status"] == "disabled"
    catalog_install = client.get(
        f"/api/v1/admin/catalog/plugin/{install.id}?workspace_id={workspace.id}",
        headers=_admin_headers(),
    )
    assert catalog_install.status_code == 200
    assert catalog_install.json()["metadata"]["platform_blocked"] is True
    session.refresh(install)
    session.refresh(credential)
    session.refresh(deployment)
    assert install.generation == 2
    assert credential.status == "revoked"
    assert deployment.desired_state == "stopped"
    assert deployment.next_check_at is not None
    with raises(ResourceAccessDenied):
        PluginServices(session).require(PluginPrincipal(workspace.id, install.id, credential.id))
    with raises(PolicyDeniedError):
        PluginService(session).action(
            workspace.id,
            owner.id,
            install.id,
            PluginAction(action="enable", expected_generation=install.generation),
        )
    session.rollback()

    released = client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/plugins/{install.id}/release",
        headers=_admin_headers(),
        json={"reason": "Review passed"},
    )
    assert released.status_code == 200
    assert released.json()["platform_blocked"] is False
    assert released.json()["status"] == "disabled"

    session.refresh(install)
    PluginService(session).action(
        workspace.id,
        owner.id,
        install.id,
        PluginAction(action="enable", expected_generation=install.generation),
    )
    deployment.desired_state = "running"
    credential.status = "active"
    session.commit()

    foreign_revoke = client.post(
        f"/api/v1/admin/workspaces/{foreign_workspace.id}/plugin-trust-keys/{trust_key.id}/revoke",
        headers=_admin_headers(),
        json={"reason": "Publisher compromised"},
    )
    revoked = client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/plugin-trust-keys/{trust_key.id}/revoke",
        headers=_admin_headers(),
        json={"reason": "Publisher compromised"},
    )
    assert foreign_revoke.status_code == 404
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert "public_key" not in revoked.json()
    session.refresh(trust_key)
    session.refresh(install)
    session.refresh(credential)
    session.refresh(deployment)
    assert trust_key.status == "revoked"
    assert install.platform_blocked is True
    assert install.status == "disabled"
    assert credential.status == "revoked"
    assert deployment.desired_state == "stopped"
    with raises(PolicyDeniedError):
        PluginService(session).action(
            workspace.id,
            owner.id,
            install.id,
            PluginAction(action="enable", expected_generation=install.generation),
        )
    session.rollback()
    actions = {
        event.action for event in session.query(AuditEvent).filter_by(workspace_id=workspace.id)
    }
    assert actions >= {
        "platform.plugin.disabled",
        "platform.plugin.released",
        "platform.plugin.publisher_key_revoked",
    }


def test_admin_global_capability_block_removes_tenant_access_and_restores_it() -> None:
    client, session, _ = _client()
    owner, workspace = _seed_workspace(session)
    capability = Capability(key="research", name="Research", category="information")
    group = ToolGroup(key="research-tools", name="Research tools", tool_names=["search"])
    skill = Skill(key="research-skill", name="Research skill", version="1.0.0")
    session.add_all([capability, group, skill])
    session.commit()
    base = f"/api/v1/workspaces/{workspace.id}/capabilities"
    member_headers = {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(owner.id)}

    assert client.get(base, headers=member_headers).json()["total"] == 1
    assert client.get(f"{base}/tool-groups", headers=member_headers).json()["total"] == 1
    assert client.get(f"{base}/skills", headers=member_headers).json()["total"] == 1
    unauthorized = client.post(
        f"/api/v1/admin/catalog/skill/{skill.id}/block", json={"reason": "Review"}
    )
    wrong_scope = client.post(
        f"/api/v1/admin/catalog/skill/{skill.id}/block?workspace_id={workspace.id}",
        headers=_admin_headers(),
        json={"reason": "Review"},
    )
    assert unauthorized.status_code == 401
    assert wrong_scope.status_code == 404

    for kind, resource_id in (
        ("capability", capability.id),
        ("tool", group.id),
        ("skill", skill.id),
    ):
        blocked = client.post(
            f"/api/v1/admin/catalog/{kind}/{resource_id}/block",
            headers=_admin_headers(),
            json={"reason": "Security review"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "disabled"
        assert blocked.json()["platform_previous_status"] == "active"
        assert blocked.json()["platform_blocked"] is True

    assert client.get(base, headers=member_headers).json()["total"] == 0
    assert client.get(f"{base}/tool-groups", headers=member_headers).json()["total"] == 0
    assert client.get(f"{base}/skills", headers=member_headers).json()["total"] == 0
    refused_install = client.post(
        f"{base}/skills/{skill.id}/install",
        headers=member_headers,
        json={},
    )
    assert refused_install.status_code == 404
    detail = client.get(f"/api/v1/admin/catalog/skill/{skill.id}", headers=_admin_headers())
    assert detail.status_code == 200
    assert detail.json()["metadata"]["platform_blocked"] is True
    assert (
        session.query(SecurityEvent)
        .filter_by(action="platform.capability.blocked", workspace_id=None)
        .count()
        == 3
    )

    for kind, resource_id in (
        ("capability", capability.id),
        ("tool", group.id),
        ("skill", skill.id),
    ):
        released = client.post(
            f"/api/v1/admin/catalog/{kind}/{resource_id}/release",
            headers=_admin_headers(),
            json={"reason": "Review passed"},
        )
        assert released.status_code == 200
        assert released.json()["status"] == "active"
        assert released.json()["platform_blocked"] is False
    assert client.get(f"{base}/skills", headers=member_headers).json()["total"] == 1
    installed = client.post(f"{base}/skills/{skill.id}/install", headers=member_headers, json={})
    assert installed.status_code == 201


def test_admin_workspace_capability_block_enforces_scope_and_live_availability() -> None:
    client, session, _ = _client()
    owner, workspace = _seed_workspace(session)
    _, foreign_workspace = _seed_workspace(
        session, email="foreign-capability@example.com", slug="foreign-capability"
    )
    skill = Skill(
        key="private-research",
        name="Private research",
        version="1.0.0",
        owner_workspace_id=workspace.id,
        visibility="private",
    )
    session.add(skill)
    session.flush()
    listing = MarketplaceListing(
        workspace_id=workspace.id,
        owner_user_id=owner.id,
        source_resource_id=skill.id,
        listing_type="skill",
        visibility="public",
        status="public",
        name="Research skill",
        version="1.0.0",
    )
    install = WorkspaceSkillInstall(
        workspace_id=workspace.id,
        skill_id=skill.id,
        installed_key=skill.key,
        installed_name=skill.name,
        installed_version=skill.version,
    )
    resource = CapabilityResource(
        workspace_id=workspace.id,
        key="knowledge",
        name="Knowledge",
        resource_type="file_collection",
    )
    server = McpServer(workspace_id=workspace.id, name="research-mcp", server_type="stdio")
    session.add_all([install, resource, server, listing])
    session.flush()
    tool = McpToolAllowlist(workspace_id=workspace.id, mcp_server_id=server.id, tool_name="search")
    session.add(tool)
    session.commit()
    base = f"/api/v1/workspaces/{workspace.id}/capabilities"
    member_headers = {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(owner.id)}
    assert client.get(f"{base}/workspace-skills", headers=member_headers).json()["total"] == 1
    assert client.get(f"{base}/resources", headers=member_headers).json()["total"] == 1
    assert len(client.get(f"{base}/mcp-tools", headers=member_headers).json()) == 1
    assert client.get("/api/v1/marketplace?listing_type=skill").json()["total"] == 1

    wrong_scope = client.post(
        f"/api/v1/admin/catalog/mcp_server/{server.id}/block?workspace_id={foreign_workspace.id}",
        headers=_admin_headers(),
        json={"reason": "Review"},
    )
    missing_scope = client.post(
        f"/api/v1/admin/catalog/mcp_server/{server.id}/block",
        headers=_admin_headers(),
        json={"reason": "Review"},
    )
    assert wrong_scope.status_code == 404
    assert missing_scope.status_code == 422

    for kind, resource_id in (
        ("skill", skill.id),
        ("skill_install", install.id),
        ("capability_resource", resource.id),
        ("mcp_server", server.id),
        ("mcp_tool", tool.id),
        ("marketplace_listing", listing.id),
    ):
        blocked = client.post(
            f"/api/v1/admin/catalog/{kind}/{resource_id}/block?workspace_id={workspace.id}",
            headers=_admin_headers(),
            json={"reason": "Security review"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["platform_blocked"] is True
        if kind == "skill":
            assert client.get("/api/v1/marketplace?listing_type=skill").json()["total"] == 0

    assert client.get(f"{base}/workspace-skills", headers=member_headers).json()["total"] == 0
    assert client.get(f"{base}/resources", headers=member_headers).json()["total"] == 0
    assert client.get(f"{base}/mcp-tools", headers=member_headers).json() == []
    assert client.get("/api/v1/marketplace?listing_type=skill").json()["total"] == 0
    tenant_disable_install = client.post(
        f"{base}/workspace-skills/{install.id}/disable", headers=member_headers
    )
    tenant_disable_server = client.post(
        f"{base}/mcp-servers/{server.id}/disable", headers=member_headers
    )
    tenant_disable_tool = client.post(
        f"{base}/mcp-servers/{server.id}/tools/{tool.id}/disable",
        headers=member_headers,
    )
    assert tenant_disable_install.status_code == 403
    assert tenant_disable_server.status_code == 403
    assert tenant_disable_tool.status_code == 403
    refused_listing_install = client.post(
        f"/api/v1/workspaces/{workspace.id}/marketplace-listings/{listing.id}/install",
        headers=member_headers,
        json={},
    )
    assert refused_listing_install.status_code == 404
    availability = client.get(
        f"{base}/workspace-skills/{install.id}/availability", headers=member_headers
    )
    assert availability.status_code == 200
    assert availability.json()["usable"] is False
    assert "platform_blocked" in availability.json()["blocked_reasons"]
    assert (
        session.query(AuditEvent)
        .filter_by(workspace_id=workspace.id, action="platform.capability.blocked")
        .count()
        == 6
    )

    for kind, resource_id in (
        ("skill", skill.id),
        ("skill_install", install.id),
        ("capability_resource", resource.id),
        ("mcp_server", server.id),
        ("mcp_tool", tool.id),
        ("marketplace_listing", listing.id),
    ):
        released = client.post(
            f"/api/v1/admin/catalog/{kind}/{resource_id}/release?workspace_id={workspace.id}",
            headers=_admin_headers(),
            json={"reason": "Review passed"},
        )
        assert released.status_code == 200
        assert released.json()["status"] == (
            "public" if kind == "marketplace_listing" else "active"
        )
    assert client.get(f"{base}/workspace-skills", headers=member_headers).json()["total"] == 1
    assert client.get(f"{base}/resources", headers=member_headers).json()["total"] == 1
    assert len(client.get(f"{base}/mcp-tools", headers=member_headers).json()) == 1
    assert client.get("/api/v1/marketplace?listing_type=skill").json()["total"] == 1


def test_admin_security_event_response_redacts_sensitive_metadata() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    session.add(
        SecurityEvent(
            workspace_id=workspace.id,
            user_id=None,
            action="runtime.policy.violation",
            outcome="denied",
            severity="critical",
            path="/api/v1/workspaces/x/runtimes",
            method="POST",
            reason="policy",
            event_metadata={
                "token": "runtime-token",
                "request_headers": {"authorization": "Bearer hidden"},
                "provider": {"base_url": "https://router.example.test/private"},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        "/api/v1/admin/security-events?action=runtime.policy.violation",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["event_metadata"]
    assert metadata == {
        "token": "[redacted]",
        "request_headers": "[redacted]",
        "provider": {"base_url": "[redacted]"},
        "safe": "visible",
    }
    assert "runtime-token" not in str(metadata)
    assert "router.example.test/private" not in str(metadata)


def test_admin_can_drain_worker_and_quarantine_runtime_space() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Unsafe Space",
        scope="workspace",
        status="active",
        policy={},
        network_policy={"mode": "none"},
        storage_policy={},
        cleanup_policy={},
    )
    worker = WorkerNode(
        worker_id="worker-1",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    session.add_all([runtime_space, worker])
    session.commit()

    drained = client.post("/api/v1/admin/workers/worker-1/drain", headers=_admin_headers())
    quarantined = client.post(
        f"/api/v1/admin/runtime-spaces/{runtime_space.id}/quarantine",
        headers=_admin_headers(),
        json={"reason": "Suspicious egress"},
    )

    session.refresh(worker)
    session.refresh(runtime_space)
    event = session.query(RuntimeSpaceEvent).one()

    assert drained.status_code == 200
    assert drained.json()["status"] == "draining"
    assert worker.status == "draining"
    assert quarantined.status_code == 200
    assert quarantined.json()["status"] == "quarantined"
    assert quarantined.json()["reason"] == "Suspicious egress"
    assert runtime_space.status == "quarantined"
    assert event.event_type == "runtime_space.quarantined"


def test_admin_can_update_worker_governance_capacity_and_status() -> None:
    client, session, _ = _client()
    worker = WorkerNode(
        worker_id="worker-governed",
        worker_type="cloud",
        status="draining",
        queue_name="agent_runs",
        capacity={"max_jobs": 1, "worker_type": "cloud"},
        details={"region": "sg"},
        last_seen_at=datetime.now(UTC),
        drain_requested_at=datetime.now(UTC),
    )
    session.add(worker)
    session.commit()

    updated = client.patch(
        "/api/v1/admin/workers/worker-governed",
        headers=_admin_headers(),
        json={
            "status": "online",
            "worker_type": "self_hosted",
            "worker_version": "2026.05.20",
            "hostname": "node-a",
            "capacity": {
                "max_jobs": 4,
                "runtime_modes": ["self_hosted", "docker"],
                "capabilities": ["code.execute", "image.generate"],
                "memory_mb": 8192,
            },
            "details": {"region": "sg", "pool": "premium", "token": "detail-token"},
            "reason": "Resize worker pool",
            "updated_by": "ops-admin",
        },
    )

    session.refresh(worker)
    policy = session.query(PlatformPolicy).filter_by(policy_key="global_worker_control").one()
    events = (
        session.query(PlatformPolicyEvent)
        .filter_by(platform_policy_id=policy.id)
        .order_by(PlatformPolicyEvent.created_at)
        .all()
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "online"
    assert updated.json()["worker_type"] == "self_hosted"
    assert updated.json()["capacity"]["max_jobs"] == 4
    assert updated.json()["capacity"]["worker_type"] == "self_hosted"
    assert updated.json()["capacity"]["runtime_modes"] == ["self_hosted", "docker"]
    assert worker.drain_requested_at is None
    assert worker.details == {"region": "sg", "pool": "premium", "token": "detail-token"}
    assert [event.event_type for event in events] == ["platform_policy.created", "worker.updated"]
    assert events[-1].event_metadata["reason"] == "Resize worker pool"
    assert events[-1].event_metadata["updated_by"] == "ops-admin"
    assert events[-1].event_metadata["changed_fields"] == [
        "status",
        "worker_type",
        "worker_version",
        "hostname",
        "capacity",
        "details",
    ]

    event_response = client.get(
        f"/api/v1/admin/platform-policies/{policy.policy_key}/events?event_type=worker.updated",
        headers=_admin_headers(),
    )
    assert event_response.status_code == 200
    metadata = event_response.json()["items"][0]["event_metadata"]
    assert metadata["after"]["details"]["token"] == "[redacted]"
    assert "detail-token" not in str(metadata)


def test_admin_worker_control_policy_is_enforced_for_worker_updates() -> None:
    client, session, _ = _client()
    worker = WorkerNode(
        worker_id="worker-policy",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 2, "memory_mb": 4096},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    session.add(worker)
    session.commit()

    policy = client.patch(
        "/api/v1/admin/platform-policies/worker-control",
        headers=_admin_headers(),
        json={
            "value": {
                "allow_status_updates": False,
                "allow_capacity_updates": True,
                "allow_queue_updates": False,
                "allowed_statuses": ["online", "draining"],
                "allowed_worker_types": ["cloud"],
                "max_capacity": {"max_jobs": 4, "memory_mb": 8192},
                "ignored": True,
            },
            "description": "Constrained worker updates",
            "updated_by": "ops-admin",
        },
    )
    denied_status = client.patch(
        "/api/v1/admin/workers/worker-policy",
        headers=_admin_headers(),
        json={"status": "draining", "reason": "Try drain"},
    )
    denied_queue = client.patch(
        "/api/v1/admin/workers/worker-policy",
        headers=_admin_headers(),
        json={"queue_name": "priority_runs", "reason": "Move queue"},
    )
    denied_capacity = client.patch(
        "/api/v1/admin/workers/worker-policy",
        headers=_admin_headers(),
        json={"capacity": {"max_jobs": 5}, "reason": "Too large"},
    )
    accepted = client.patch(
        "/api/v1/admin/workers/worker-policy",
        headers=_admin_headers(),
        json={"capacity": {"max_jobs": 4, "memory_mb": 8192}, "reason": "Allowed resize"},
    )

    session.refresh(worker)

    assert policy.status_code == 200
    assert policy.json()["policy_key"] == "global_worker_control"
    assert policy.json()["description"] == "Constrained worker updates"
    assert policy.json()["value"]["allow_status_updates"] is False
    assert policy.json()["value"]["allow_queue_updates"] is False
    assert policy.json()["value"]["allowed_statuses"] == ["online", "draining"]
    assert policy.json()["value"]["allowed_worker_types"] == ["cloud"]
    assert policy.json()["value"]["max_capacity"] == {"max_jobs": 4, "memory_mb": 8192}
    assert "ignored" not in policy.json()["value"]
    assert denied_status.status_code == 403
    assert denied_status.json()["error"]["code"] == "worker_status_update_denied"
    assert denied_queue.status_code == 403
    assert denied_queue.json()["error"]["code"] == "worker_queue_update_denied"
    assert denied_capacity.status_code == 403
    assert denied_capacity.json()["error"]["code"] == "worker_capacity_exceeds_policy"
    assert denied_capacity.json()["error"]["details"] == {
        "capacity_key": "max_jobs",
        "requested": 5,
        "max_allowed": 4,
    }
    assert accepted.status_code == 200
    assert accepted.json()["capacity"]["max_jobs"] == 4
    assert worker.status == "online"
    assert worker.queue_name == "agent_runs"
    assert worker.capacity["max_jobs"] == 4


def test_admin_can_list_platform_policy_events() -> None:
    client, session, _ = _client()
    policy = client.patch(
        "/api/v1/admin/platform-policies/worker-control",
        headers=_admin_headers(),
        json={
            "value": {"allow_queue_updates": False},
            "description": "Audit worker policy",
            "updated_by": "ops-admin",
        },
    )

    events = client.get(
        "/api/v1/admin/platform-policies/global_worker_control/events",
        headers=_admin_headers(),
    )
    filtered = client.get(
        "/api/v1/admin/platform-policies/global_worker_control/events"
        "?event_type=platform_policy.updated",
        headers=_admin_headers(),
    )
    missing = client.get(
        "/api/v1/admin/platform-policies/missing-policy/events",
        headers=_admin_headers(),
    )

    stored_policy = (
        session.query(PlatformPolicy)
        .filter_by(
            policy_key="global_worker_control",
        )
        .one()
    )

    assert policy.status_code == 200
    assert events.status_code == 200
    assert events.json()["total"] == 2
    assert events.json()["items"][0]["platform_policy_id"] == str(stored_policy.id)
    assert events.json()["items"][0]["event_type"] == "platform_policy.updated"
    assert events.json()["items"][0]["event_metadata"]["updated_by"] == "ops-admin"
    assert events.json()["items"][1]["event_type"] == "platform_policy.created"
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["event_type"] == "platform_policy.updated"
    assert missing.status_code == 404


def test_admin_worker_control_policy_rejects_disallowed_worker_type() -> None:
    client, session, _ = _client()
    worker = WorkerNode(
        worker_id="worker-type-policy",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    session.add(worker)
    session.commit()

    policy = client.get(
        "/api/v1/admin/platform-policies/worker-control",
        headers=_admin_headers(),
    )
    denied = client.patch(
        "/api/v1/admin/workers/worker-type-policy",
        headers=_admin_headers(),
        json={"worker_type": "gpu", "reason": "Unsupported pool"},
    )

    assert policy.status_code == 200
    assert policy.json()["value"]["allowed_worker_types"] == ["cloud", "self_hosted"]
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "worker_type_not_allowed"
    assert denied.json()["error"]["details"]["allowed_worker_types"] == [
        "cloud",
        "self_hosted",
    ]


def test_admin_can_manage_global_queue_runtime_and_risky_execution_policy() -> None:
    client, session, redis = _client()
    _, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Team runtime",
        status="running",
        connection_status="online",
        docker_container_id="container-123",
        limits={"cpu_count": 1, "memory_mb": 512},
        network_policy={"mode": "none"},
        capabilities={},
    )
    session.add(runtime)
    session.flush()
    runtime_lease = RuntimeLease(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        docker_container_id="container-123",
        status="running",
        lease_metadata={"purpose": "admin-test"},
        acquired_at=datetime.now(UTC),
    )
    session.add(runtime_lease)
    session.commit()
    queue = RedisQueue(redis, RedisKeyBuilder("opsmesh"), "agent_runs", 0)
    queued = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="queued-job",
    )
    dead = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.RUNTIME_CLEANUP,
        resource_id=runtime.id,
        idempotency_key="dead-job",
        attempt=2,
    )
    queue.enqueue(queued)
    queue.retry_or_dead_letter(dead)

    metrics = client.get("/api/v1/admin/queues/agent_runs/metrics", headers=_admin_headers())
    dead_letters = client.get(
        "/api/v1/admin/queues/agent_runs/dead-letter-jobs",
        headers=_admin_headers(),
    )
    requeued = client.post(
        f"/api/v1/admin/queues/agent_runs/dead-letter-jobs/{dead.job_id}/requeue",
        headers=_admin_headers(),
    )
    runtimes = client.get("/api/v1/admin/runtimes", headers=_admin_headers())
    stopped = client.post(
        f"/api/v1/admin/runtimes/{runtime.id}/force-stop",
        headers=_admin_headers(),
        json={"reason": "Operator safety stop"},
    )
    policy = client.get(
        "/api/v1/admin/platform-policies/risky-execution",
        headers=_admin_headers(),
    )
    updated_policy = client.patch(
        "/api/v1/admin/platform-policies/risky-execution",
        headers=_admin_headers(),
        json={
            "value": {
                "allow_runtime_commands": True,
                "allow_network_egress": False,
                "high_risk_tool_mode": "allow",
                "unknown": True,
            },
            "description": "Test policy",
            "updated_by": "admin-test",
        },
    )

    session.refresh(runtime)
    session.refresh(runtime_lease)
    runtime_event = session.query(RuntimeEvent).filter_by(workspace_runtime_id=runtime.id).one()

    assert metrics.status_code == 200
    assert metrics.json()["queued"] == 1
    assert metrics.json()["dead_letter"] == 1
    assert dead_letters.status_code == 200
    assert dead_letters.json()["total"] == 1
    assert requeued.status_code == 200
    assert requeued.json()["requeued"] is True
    admin_queue = RedisQueue(redis, RedisKeyBuilder("opsmesh"), "agent_runs", 0)
    assert admin_queue.count_dead_letters() == 0
    assert runtimes.status_code == 200
    assert runtimes.json()["items"][0]["id"] == str(runtime.id)
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopping"
    assert runtime.status == "stopping"
    assert runtime.connection_status == "degraded"
    assert runtime_lease.status == "running"
    assert runtime_lease.released_at is None
    assert runtime_lease.lease_metadata["stop_requested_by"] == "platform_admin"
    assert runtime_lease.lease_metadata["stop_request_reason"] == "Operator safety stop"
    assert runtime_event.event_type == "runtime.force_stop_requested"
    assert runtime_event.event_metadata["runtime_lease_id"] == str(runtime_lease.id)
    assert runtime_event.event_metadata["runtime_lease_release_pending"] is True
    assert runtime_event.event_metadata["worker_control_enqueued"] is True
    stop_job = admin_queue.dequeue()
    assert stop_job is not None
    assert stop_job.job_type == JobType.RUNTIME_CONTROL
    assert stop_job.resource_id == runtime.id
    assert stop_job.routing["action"] == "stop"
    assert stop_job.routing["source"] == "platform_admin"
    assert policy.status_code == 200
    assert policy.json()["policy_key"] == "global_risky_execution"
    assert updated_policy.status_code == 200
    assert updated_policy.json()["description"] == "Test policy"
    assert updated_policy.json()["updated_by"] == "admin-test"
    assert updated_policy.json()["value"]["allow_runtime_commands"] is True
    assert updated_policy.json()["value"]["high_risk_tool_mode"] == "allow"
    assert updated_policy.json()["value"]["require_approval_for_high_risk_tools"] is False
    assert "unknown" not in updated_policy.json()["value"]


def test_admin_operations_summary_aggregates_queue_capacity_and_blockers() -> None:
    client, session, redis = _client()
    _, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Space",
        scope="team",
        status="active",
        policy={},
        network_policy={},
        storage_policy={},
        cleanup_policy={},
    )
    worker = WorkerNode(
        worker_id="worker-summary",
        worker_type="cloud",
        status="online",
        queue_name="agent_runs",
        capacity={"max_jobs": 3},
        details={},
        last_seen_at=datetime.now(UTC),
    )
    failed_run = AgentRun(
        workspace_id=workspace.id,
        status="failed",
        input={},
        error={"code": "model_timeout"},
    )
    waiting_run = AgentRun(
        workspace_id=workspace.id,
        status="waiting_approval",
        input={},
    )
    waiting_task = Task(
        workspace_id=workspace.id,
        title="Needs approval",
        status="waiting_approval",
    )
    approval = Approval(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=waiting_run.id,
        approval_type="mcp.tool",
        risk_level="high",
        payload={},
        status="pending",
        created_at=datetime.now(UTC),
    )
    security_event = SecurityEvent(
        workspace_id=workspace.id,
        user_id=None,
        action="mcp_tool.blocked",
        outcome="blocked",
        severity="high",
        path="internal:mcp",
        method="WORKER",
        reason="mcp_tool_not_allowed",
        event_metadata={},
        created_at=datetime.now(UTC),
    )
    session.add_all([runtime_space, worker, failed_run, waiting_run, waiting_task])
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=4,
        reserved_value=2,
        unit="count",
    )
    lease = WorkerLease(
        workspace_id=workspace.id,
        worker_id=worker.worker_id,
        queue_name="agent_runs",
        job_id=uuid4(),
        job_type="agent.run",
        resource_id=waiting_run.id,
        status="running",
        attempt=0,
        lease_metadata={},
        started_at=datetime.now(UTC),
    )
    approval.agent_run_id = waiting_run.id
    session.add_all([quota, lease, approval, security_event])
    session.commit()
    queue = RedisQueue(redis, RedisKeyBuilder("opsmesh"), "agent_runs", 0)
    high_priority_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="high-priority",
        priority=9,
    )
    low_priority_job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="low-priority",
        priority=1,
    )
    queue.enqueue(low_priority_job)
    queue.enqueue(high_priority_job)

    response = client.get(
        "/api/v1/admin/operations/summary?queue_name=agent_runs",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["queue"]["queued"] == 2
    assert body["queue"]["highest_priority"] == 9
    assert body["queue"]["oldest_queued_at"] is not None
    assert body["workers"]["total"] == 1
    assert body["workers"]["running_leases"] == 1
    assert body["workers"]["total_capacity"] == 3
    assert body["workers"]["available_capacity"] == 2
    assert body["runtime_spaces"]["quota_usage"]["active_runs"]["limit_value"] == 4
    assert body["runtime_spaces"]["quota_usage"]["active_runs"]["reserved_value"] == 2
    assert body["approvals"]["pending"] == 1
    assert body["approvals"]["runs_waiting"] == 1
    assert body["approvals"]["tasks_waiting"] == 1
    assert body["failures"]["failed_runs"] == 1
    assert body["failures"]["top_run_error_codes"] == [{"key": "model_timeout", "count": 1}]
    assert body["failures"]["top_security_reasons"] == [{"key": "mcp_tool_not_allowed", "count": 1}]


def test_admin_system_configuration_exposes_redacted_resource_summary() -> None:
    client, _, _ = _client()

    response = client.get("/api/v1/admin/system/configuration", headers=_admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["environment"] == "test"
    assert body["settings"]["database_url"].startswith("postgresql+psycopg://***:***@")
    assert "test-token" not in str(body)
    assert "admin-token" not in str(body)
    assert body["recommended_resources"]["cpu_count"] >= 1
    assert body["configured_resources"]["database_pool_size"] >= 1
    assert body["blocking_executor"]["initialized"] is False
    assert body["blocking_executor"]["configured_workers"] >= 1
    assert body["database_pool"]["backend"] in {"postgresql", "sqlite"}
    assert body["database_pool"]["pool_class"]
    assert body["redis_pool"]["max_connections"] is not None
    assert set(body["resource_deltas"]) == {
        "database_pool_size",
        "database_max_overflow",
        "blocking_thread_pool_workers",
        "redis_max_connections",
    }
    assert body["settings"]["release_update_enabled"] is False
    assert body["settings"]["release_update_repository"] == "jhupo/OpsMesh"
    assert body["settings"]["resolved_feature_flags"]["docker_runtimes"] == {
        "key": "docker_runtimes",
        "enabled": True,
        "source": "default",
        "reason": "known default",
    }


def test_admin_system_version_reports_current_package_version() -> None:
    client, _, _ = _client()

    response = client.get("/api/v1/admin/system/version", headers=_admin_headers())

    assert response.status_code == 200
    assert response.json()["tag"].startswith("v")


def test_admin_check_updates_returns_latest_release(monkeypatch) -> None:
    from backend.app.platform.releases.cache import clear_release_update_cache
    from backend.app.platform.releases.models import (
        ReleaseAsset,
        ReleaseUpdateCheck,
        ReleaseVersion,
    )
    from backend.app.platform.releases.service import ReleaseUpdateService

    clear_release_update_cache()

    def fake_fetch(self: ReleaseUpdateService) -> ReleaseUpdateCheck:
        current = self.current_version()
        return ReleaseUpdateCheck(
            current=current,
            latest=ReleaseVersion(version="9.9.9", tag="v9.9.9", commit="abc123"),
            update_available=True,
            release_url="https://github.com/jhupo/OpsMesh/releases/tag/v9.9.9",
            assets=[
                ReleaseAsset(
                    name="opsmesh-server-v9.9.9.tar.gz",
                    browser_download_url=(
                        "https://github.com/jhupo/OpsMesh/releases/download/v9.9.9/"
                        "opsmesh-server-v9.9.9.tar.gz"
                    ),
                    size=123,
                    content_type="application/gzip",
                    digest="sha256:asset-digest",
                )
            ],
            cached=False,
        )

    monkeypatch.setattr(ReleaseUpdateService, "_fetch_latest_release", fake_fetch)
    client, _, _ = _client()

    first = client.get("/api/v1/admin/system/check-updates", headers=_admin_headers())
    second = client.get("/api/v1/admin/system/check-updates", headers=_admin_headers())

    assert first.status_code == 200
    assert first.json()["latest"]["tag"] == "v9.9.9"
    assert first.json()["update_available"] is True
    assert first.json()["assets"] == [
        {
            "name": "opsmesh-server-v9.9.9.tar.gz",
            "browser_download_url": (
                "https://github.com/jhupo/OpsMesh/releases/download/v9.9.9/"
                "opsmesh-server-v9.9.9.tar.gz"
            ),
            "size": 123,
            "content_type": "application/gzip",
            "digest": "sha256:asset-digest",
        }
    ]
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True


def test_admin_can_manage_user_lifecycle_and_workspace_membership() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)

    created = client.post(
        "/api/v1/admin/users",
        headers=_admin_headers(),
        json={
            "email": "managed@example.com",
            "display_name": "Managed User",
            "username": "managed-user",
        },
    )
    assert created.status_code == 201
    created_body = created.json()
    user_id = created_body["id"]
    assert created_body["initial_password"]

    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "managed@example.com",
            "password": created_body["initial_password"],
        },
    )
    assert login.status_code == 200
    assert login.json()["user_id"] == user_id

    detail = client.get(f"/api/v1/admin/users/{user_id}", headers=_admin_headers())
    assert detail.status_code == 200
    assert detail.json()["workspace_memberships"] == []

    added = client.post(
        f"/api/v1/admin/workspaces/{workspace.id}/members",
        headers=_admin_headers(),
        json={"user_id": user_id, "role": "viewer"},
    )
    assert added.status_code == 201
    member_id = added.json()["id"]

    session.add(
        WorkspaceQuota(
            workspace_id=workspace.id,
            quota_key="active_runs",
            limit_value=10,
            reserved_value=4,
            unit="count",
            status="active",
        )
    )
    session.add(
        WorkspaceQuota(
            workspace_id=workspace.id,
            quota_key="docker_runtimes",
            limit_value=10,
            reserved_value=9,
            unit="count",
            status="active",
        )
    )
    session.commit()
    listed = client.get("/api/v1/admin/users", headers=_admin_headers())
    assert listed.status_code == 200
    managed_summary = next(item for item in listed.json()["items"] if item["id"] == user_id)
    assert managed_summary["workspace_count"] == 1
    assert managed_summary["active_workspace_count"] == 1
    assert managed_summary["resource_usage_rate"] == 0.9

    updated_member = client.patch(
        f"/api/v1/admin/workspaces/{workspace.id}/members/{member_id}",
        headers=_admin_headers(),
        json={"role": "operator"},
    )
    assert updated_member.status_code == 200
    assert updated_member.json()["role"] == "operator"

    token = UserAPIToken(
        user_id=UUID(user_id),
        name="test token",
        token_hash="hash-managed-user",
        fingerprint="fingerprint-managed-user",
        status="active",
    )
    session.add(token)
    session.commit()
    revoked = client.post(
        f"/api/v1/admin/users/{user_id}/revoke-tokens",
        headers=_admin_headers(),
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] == 2
    session.refresh(token)
    assert token.status == "revoked"

    session.add(
        UserAPIToken(
            user_id=UUID(user_id),
            name="reset token",
            token_hash="hash-reset-token",
            fingerprint="fingerprint-reset-token",
            status="active",
        )
    )
    session.commit()
    reset = client.post(
        f"/api/v1/admin/users/{user_id}/reset-password",
        headers=_admin_headers(),
    )
    assert reset.status_code == 200
    assert reset.json()["temporary_password"]
    assert (
        session.query(UserAPIToken)
        .filter_by(
            user_id=UUID(user_id),
            status="active",
        )
        .count()
        == 0
    )
    reset_login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "managed@example.com",
            "password": reset.json()["temporary_password"],
        },
    )
    assert reset_login.status_code == 200

    secondary = client.post(
        "/api/v1/admin/users",
        headers=_admin_headers(),
        json={"email": "secondary@example.com", "display_name": "Secondary User"},
    )
    assert secondary.status_code == 201
    disabled = client.put(
        f"/api/v1/admin/users/{secondary.json()['id']}/status",
        headers=_admin_headers(),
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    promoted = client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=_admin_headers(),
        json={"display_name": "Managed Admin", "platform_admin": True},
    )
    assert promoted.status_code == 200
    assert promoted.json()["platform_admin"] is True
    cannot_demote_last_admin = client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=_admin_headers(),
        json={"platform_admin": False},
    )
    cannot_disable_last_admin = client.put(
        f"/api/v1/admin/users/{user_id}/status",
        headers=_admin_headers(),
        json={"status": "disabled"},
    )
    assert cannot_demote_last_admin.status_code == 409
    assert cannot_disable_last_admin.status_code == 409

    removed = client.delete(
        f"/api/v1/admin/workspaces/{workspace.id}/members/{member_id}",
        headers=_admin_headers(),
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "disabled"

    actions = {
        event.action
        for event in session.query(SecurityEvent)
        if event.action.startswith("identity.")
    }
    assert actions >= {
        "identity.user_created",
        "identity.user_updated",
        "identity.user_password_reset",
        "identity.user_tokens_revoked",
        "identity.user_status_changed",
    }
    workspace_actions = {
        event.action for event in session.query(AuditEvent) if event.workspace_id == workspace.id
    }
    assert workspace_actions >= {
        "platform.workspace.member_added",
        "platform.workspace.member_updated",
        "platform.workspace.member_removed",
    }


def _client() -> tuple[TestClient, Session, fakeredis.FakeRedis]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    redis = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            platform_admin_token=ADMIN_TOKEN,
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
    app.dependency_overrides[get_worker_queue] = lambda: RedisQueue(
        redis,
        RedisKeyBuilder(app.state.settings.redis_key_prefix),
        app.state.settings.worker_queue_name,
        0,
    )
    return TestClient(app), session, redis


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


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
