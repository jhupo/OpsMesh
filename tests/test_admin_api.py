import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
from opsmesh_plugin_sdk.packaging.packages import sign_package
from pytest import raises

from opsmesh.capabilities.catalog.models import Capability, ToolGroup
from opsmesh.capabilities.marketplace.models import MarketplaceListing
from opsmesh.capabilities.mcp.models import MCPServer, MCPToolAllowlist
from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.capabilities.plugins.contracts import PluginActionRequest
from opsmesh.capabilities.plugins.lifecycle import PluginLifecycleService
from opsmesh.capabilities.plugins.models import (
    PluginCredential,
    PluginInstallation,
    PluginRelease,
    PluginTrustKey,
)
from opsmesh.capabilities.references.models import CapabilityResource
from opsmesh.capabilities.skills.models import Skill, WorkspaceSkillInstall
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.policies.models import PlatformPolicy
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.auth.models import UserAPIToken
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.runtime.instances.models import RuntimeEvent, WorkspaceRuntime
from opsmesh.runtime.spaces.models import RuntimeSpace, RuntimeSpaceEvent
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.shared.utils import payload_hash
from opsmesh.workspaces.members.models import WorkspaceMember
from opsmesh.workspaces.projects.models import WorkspaceProject, WorkspaceProjectQuota
from opsmesh.workspaces.quotas.models import WorkspaceQuota
from opsmesh.workspaces.quotas.reservations import (
    WorkspaceQuotaReservationService as WorkspaceQuotaReservationService,
)
from tests.fixtures.admin_api import TOKEN, _admin_headers, _client, _seed_workspace


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

    from opsmesh.identity.auth.service import AuthenticationService

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
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    space = RuntimeSpace(workspace_id=workspace.id, name="Team Space", scope="workspace")
    session.add(space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=space.id,
        name="Team Runtime",
        sandbox_options={"image": "opsmesh-runtime:test"},
    )
    event = SecurityEvent(
        workspace_id=other_workspace.id,
        action="runtime.policy.violation",
        outcome="denied",
        severity="critical",
        path="internal:sandbox",
        method="ACTIVITY",
        reason="policy_denied",
        event_metadata={},
        created_at=datetime.now(UTC),
    )
    session.add_all([runtime, event])
    session.flush()
    stage_execution(
        session,
        ExecutionRequest(
            workspace_id=workspace.id,
            resource_id=runtime.id,
            kind=ExecutionKind.SANDBOX_CLEANUP,
        ),
    )
    session.commit()

    overview = client.get("/api/v1/admin/overview", headers=_admin_headers())
    runtimes = client.get("/api/v1/admin/runtimes", headers=_admin_headers())
    spaces = client.get("/api/v1/admin/runtime-spaces", headers=_admin_headers())
    events = client.get("/api/v1/admin/security-events?severity=critical", headers=_admin_headers())
    workspaces = client.get("/api/v1/admin/workspaces", headers=_admin_headers())

    assert overview.status_code == 200
    assert overview.json()["workspaces_total"] == 2
    assert overview.json()["runtimes_active"] == 1
    assert overview.json()["temporal_dispatch_pending"] == 1
    assert overview.json()["critical_security_events"] == 1
    assert runtimes.status_code == 200
    assert runtimes.json()["items"][0]["id"] == str(runtime.id)
    assert spaces.status_code == 200
    assert spaces.json()["items"][0]["id"] == str(space.id)
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
    private_key = Ed25519PrivateKey.generate()
    package = sign_package(
        PluginManifest.model_validate(
            {
                "key": "incident-channel",
                "version": "1.0.0",
                "name": "Incident channel",
                "capabilities": [
                    {
                        "key": "messages",
                        "kind": "channel",
                        "title": "Messages",
                        "features": ["receive", "send"],
                        "required_permissions": ["messages.receive"],
                    }
                ],
            }
        ),
        "publisher-a",
        private_key.private_bytes_raw(),
    )
    trust_key = PluginTrustKey(
        workspace_id=workspace.id,
        key_id="publisher-a",
        plugin_key="incident-channel",
        public_key=base64.b64encode(private_key.public_key().public_bytes_raw()).decode(),
    )
    session.add(trust_key)
    session.flush()
    release = PluginRelease(
        workspace_id=workspace.id,
        plugin_key="incident-channel",
        trust_key_id=trust_key.id,
        version="1.0.0",
        package=package.model_dump(mode="json"),
        checksum=payload_hash(package.model_dump(mode="json")),
    )
    session.add(release)
    session.flush()
    install = PluginInstallation(
        workspace_id=workspace.id,
        plugin_key="incident-channel",
        instance_name="incident-production",
        current_version="1.0.0",
        current_release_id=release.id,
        approved_permissions=["messages.receive"],
        status="active",
        generation=1,
    )
    session.add(install)
    session.flush()
    credential = PluginCredential(
        workspace_id=workspace.id,
        install_id=install.id,
        generation=1,
        token_hash="b" * 64,
        permissions=["messages.receive"],
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(credential)
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
    assert install.generation == 2
    assert credential.status == "revoked"
    with raises(ResourceAccessDeniedError):
        PluginAuthenticationService(session).require(
            PluginPrincipal(workspace.id, install.id, credential.id)
        )
    with raises(PolicyDeniedError):
        PluginLifecycleService(session).action(
            workspace.id,
            owner.id,
            install.id,
            PluginActionRequest(action="enable", expected_generation=install.generation),
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
    PluginLifecycleService(session).action(
        workspace.id,
        owner.id,
        install.id,
        PluginActionRequest(action="enable", expected_generation=install.generation),
    )
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
    assert trust_key.status == "revoked"
    assert install.platform_blocked is True
    assert install.status == "disabled"
    assert credential.status == "revoked"
    with raises(PolicyDeniedError):
        PluginLifecycleService(session).action(
            workspace.id,
            owner.id,
            install.id,
            PluginActionRequest(action="enable", expected_generation=install.generation),
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
    server = MCPServer(workspace_id=workspace.id, name="research-mcp", server_type="stdio")
    session.add_all([install, resource, server, listing])
    session.flush()
    tool = MCPToolAllowlist(workspace_id=workspace.id, mcp_server_id=server.id, tool_name="search")
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


def test_admin_can_quarantine_runtime_space() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Unsafe Space", scope="workspace")
    session.add(runtime_space)
    session.commit()

    response = client.post(
        f"/api/v1/admin/runtime-spaces/{runtime_space.id}/quarantine",
        headers=_admin_headers(),
        json={"reason": "Suspicious execution"},
    )
    session.refresh(runtime_space)
    event = session.query(RuntimeSpaceEvent).one()
    assert response.status_code == 200
    assert response.json()["status"] == "quarantined"
    assert runtime_space.status == "quarantined"
    assert event.event_type == "runtime_space.quarantined"
    assert event.message == "Suspicious execution"


def test_admin_can_list_platform_policy_events() -> None:
    client, session, _ = _client()
    policy = client.patch(
        "/api/v1/admin/platform-policies/risky-execution",
        headers=_admin_headers(),
        json={
            "value": {"allow_runtime_commands": True},
            "description": "Audit worker policy",
            "updated_by": "ops-admin",
        },
    )

    events = client.get(
        "/api/v1/admin/platform-policies/global_risky_execution/events",
        headers=_admin_headers(),
    )
    filtered = client.get(
        "/api/v1/admin/platform-policies/global_risky_execution/events"
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
            policy_key="global_risky_execution",
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


def test_admin_can_stop_native_runtime_and_manage_risky_execution_policy() -> None:
    client, session, _ = _client()
    _, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Team Runtime",
        sandbox_options={"image": "runtime:test"},
    )
    session.add(runtime)
    session.commit()
    runtimes = client.get("/api/v1/admin/runtimes", headers=_admin_headers())
    stopped = client.post(
        f"/api/v1/admin/runtimes/{runtime.id}/force-stop",
        headers=_admin_headers(),
        json={"reason": "Operator safety stop"},
    )
    policy = client.get("/api/v1/admin/platform-policies/risky-execution", headers=_admin_headers())
    updated = client.patch(
        "/api/v1/admin/platform-policies/risky-execution",
        headers=_admin_headers(),
        json={
            "value": {"allow_runtime_commands": True, "high_risk_tool_mode": "allow"},
            "description": "Test policy",
            "updated_by": "admin-test",
        },
    )

    session.refresh(runtime)
    event = session.query(RuntimeEvent).filter_by(workspace_runtime_id=runtime.id).one()
    dispatch = session.query(TemporalOutboxEntry).one()
    assert runtimes.status_code == 200
    assert runtimes.json()["items"][0]["id"] == str(runtime.id)
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "disabled"
    assert runtime.status == "disabled"
    assert event.event_type == "runtime.force_stop_requested"
    assert event.event_metadata["sandbox_cleanup_staged"] is True
    assert dispatch.operation == "start"
    assert dispatch.payload["execution"]["resource_id"] == str(runtime.id)
    assert dispatch.payload["execution"]["kind"] == ExecutionKind.SANDBOX_CLEANUP.value
    assert policy.status_code == 200
    assert policy.json()["policy_key"] == "global_risky_execution"
    assert updated.status_code == 200
    assert updated.json()["value"]["allow_runtime_commands"] is True
    assert updated.json()["value"]["high_risk_tool_mode"] == "allow"
    assert updated.json()["value"]["require_approval_for_high_risk_tools"] is False


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
    assert body["database_pool"]["backend"] == "postgresql"
    assert body["database_pool"]["pool_class"]
    assert body["redis_pool"]["max_connections"] is not None
    assert set(body["resource_deltas"]) == {
        "database_pool_size",
        "database_max_overflow",
        "redis_max_connections",
    }
    assert body["settings"]["release_update_enabled"] is False
    assert body["settings"]["release_update_repository"] == "jhupo/OpsMesh"
    assert "resolved_feature_flags" not in body["settings"]


def test_admin_system_version_reports_current_package_version() -> None:
    client, _, _ = _client()

    response = client.get("/api/v1/admin/system/version", headers=_admin_headers())

    assert response.status_code == 200
    assert response.json()["tag"].startswith("v")


def test_admin_check_updates_returns_latest_release(monkeypatch) -> None:
    from opsmesh.platform.releases.cache import clear_release_update_cache
    from opsmesh.platform.releases.models import (
        ReleaseAsset,
        ReleaseUpdateCheck,
        ReleaseVersion,
    )
    from opsmesh.platform.releases.service import ReleaseCheckService

    clear_release_update_cache()

    def fake_fetch(self: ReleaseCheckService) -> ReleaseUpdateCheck:
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

    monkeypatch.setattr(ReleaseCheckService, "_fetch_latest_release", fake_fetch)
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
            quota_key="runtime_shell",
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
