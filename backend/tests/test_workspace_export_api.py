import json
from collections.abc import Generator
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import Skill, WorkspaceSkillInstall
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workers.runner import WorkerRunner, WorkerRunnerConfig
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_workspace_metadata_export_is_scoped_and_audited(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner-space")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other-space")
    agent = AgentProfile(workspace_id=workspace.id, name="Researcher", role="researcher")
    other_agent = AgentProfile(workspace_id=other_workspace.id, name="Other", role="researcher")
    team = AgentTeam(workspace_id=workspace.id, name="Research Team", team_type="research")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Q2 Research",
        team_snapshot={"team": {"name": "Research Team"}},
        project_plan={"work_packages": [{"package_id": "research"}]},
    )
    session.add_all([agent, other_agent, team, task])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="researcher",
            department="Research",
            position_title="Research Specialist",
            responsibilities=["collect sources"],
            skill_weights={"research": 0.9},
            availability={"timezone": "UTC"},
            max_concurrent_tasks=2,
        )
    )
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="research-1",
        required_role="researcher",
        required_skills=["research"],
        expected_artifacts=["work_summary"],
        acceptance_criteria=["Summary is complete."],
        review_policy={"reviewer": "manager"},
        title="Research",
    )
    session.add(step)
    session.flush()
    session.add(
        TaskMessage(
            workspace_id=workspace.id,
            task_id=task.id,
            task_step_id=step.id,
            agent_profile_id=agent.id,
            message_type="step.completed",
            sequence=1,
            body="Research summary is ready.",
            payload={"work_package_id": "research-1", "quality_score": 0.92},
        )
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/metadata",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "filename=\"owner-space-workspace-export.json\"" in response.headers[
        "content-disposition"
    ]
    payload = json.loads(response.content)
    assert payload["manifest"]["format_version"] == "workspace-export.v1"
    assert payload["workspace"]["id"] == str(workspace.id)
    assert payload["manifest"]["counts"]["agents"] == 1
    assert payload["manifest"]["counts"]["teams"] == 1
    assert payload["manifest"]["counts"]["team_members"] == 1
    assert payload["manifest"]["counts"]["tasks"] == 1
    assert payload["manifest"]["counts"]["task_messages"] == 1
    assert payload["tasks"][0]["team_snapshot"] == {"team": {"name": "Research Team"}}
    assert payload["tasks"][0]["project_plan"] == {
        "work_packages": [{"package_id": "research"}]
    }
    assert payload["task_steps"][0]["work_package_id"] == "research-1"
    assert payload["task_steps"][0]["required_role"] == "researcher"
    assert payload["task_steps"][0]["required_skills"] == ["research"]
    assert payload["task_steps"][0]["expected_artifacts"] == ["work_summary"]
    assert payload["task_steps"][0]["acceptance_criteria"] == ["Summary is complete."]
    assert payload["task_steps"][0]["review_policy"] == {"reviewer": "manager"}
    assert payload["task_messages"][0]["task_id"] == str(task.id)
    assert payload["task_messages"][0]["task_step_id"] == str(step.id)
    assert payload["task_messages"][0]["agent_profile_id"] == str(agent.id)
    assert payload["task_messages"][0]["message_type"] == "step.completed"
    assert payload["task_messages"][0]["sequence"] == 1
    assert payload["task_messages"][0]["body"] == "Research summary is ready."
    assert payload["task_messages"][0]["payload"] == {
        "work_package_id": "research-1",
        "quality_score": 0.92,
    }
    assert payload["agents"][0]["id"] == str(agent.id)
    assert payload["team_members"][0]["department"] == "Research"
    assert payload["team_members"][0]["position_title"] == "Research Specialist"
    assert payload["team_members"][0]["responsibilities"] == ["collect sources"]
    assert payload["team_members"][0]["skill_weights"] == {"research": 0.9}
    assert payload["team_members"][0]["availability"] == {"timezone": "UTC"}
    assert payload["team_members"][0]["max_concurrent_tasks"] == 2
    assert payload["team_members"][0]["accepts_tasks"] is True
    assert payload["team_members"][0]["status"] == "active"
    assert all(item["workspace_id"] == str(workspace.id) for item in payload["agents"])
    assert other_agent.id not in {item["id"] for item in payload["agents"]}

    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.export.created",
        )
    )
    assert audit is not None
    assert audit.user_id == owner.id


def test_workspace_metadata_export_denies_cross_workspace_access(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    response = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/exports/metadata",
        headers=_headers(owner.id),
        json={},
    )

    assert response.status_code == 403
    assert workspace.id != other_workspace.id


def test_workspace_metadata_import_supports_dry_run_and_committed_import(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source@example.com",
        slug="source",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target@example.com",
        slug="target",
    )
    agent = AgentProfile(workspace_id=source_workspace.id, name="Researcher", role="researcher")
    team = AgentTeam(workspace_id=source_workspace.id, name="Research Team", team_type="research")
    task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        title="Q2 Research",
        domain_type="research",
        team_snapshot={"team": {"name": "Research Team"}},
        project_plan={"work_packages": [{"package_id": "research"}]},
    )
    session.add_all([agent, team, task])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=source_workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="researcher",
            department="Research",
            responsibilities=["collect sources"],
            skill_weights={"research": 0.9},
            max_concurrent_tasks=2,
        )
    )
    step = TaskStep(
        workspace_id=source_workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="research-1",
        required_role="researcher",
        required_skills=["research"],
        expected_artifacts=["work_summary"],
        acceptance_criteria=["Summary is complete."],
        review_policy={"reviewer": "manager"},
        title="Research",
    )
    session.add(step)
    session.flush()
    session.add(
        TaskMessage(
            workspace_id=source_workspace.id,
            task_id=task.id,
            task_step_id=step.id,
            agent_profile_id=agent.id,
            message_type="step.completed",
            sequence=1,
            body="Research package completed.",
            payload={"work_package_id": "research-1"},
        )
    )
    session.commit()
    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False, "include_runs": False, "include_files": False},
    )
    export_payload = json.loads(export_response.content)

    dry_run = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )
    after_dry_run_agents = session.scalars(
        select(AgentProfile).where(AgentProfile.workspace_id == target_workspace.id)
    ).all()
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert dry_run.status_code == 200
    assert dry_run.json()["created_counts"]["agents"] == 1
    assert dry_run.json()["created_counts"]["task_messages"] == 1
    assert after_dry_run_agents == []
    assert committed.status_code == 200
    body = committed.json()
    assert body["dry_run"] is False
    assert body["created_counts"]["agents"] == 1
    assert body["created_counts"]["teams"] == 1
    assert body["created_counts"]["team_members"] == 1
    assert body["created_counts"]["tasks"] == 1
    assert body["created_counts"]["task_steps"] == 1
    assert body["created_counts"]["task_messages"] == 1
    assert len(body["id_map"]["agents"]) == 1

    imported_agent = session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == target_workspace.id,
            AgentProfile.name == "Imported Researcher",
        )
    )
    imported_team = session.scalar(
        select(AgentTeam).where(
            AgentTeam.workspace_id == target_workspace.id,
            AgentTeam.name == "Imported Research Team",
        )
    )
    imported_task = session.scalar(
        select(Task).where(
            Task.workspace_id == target_workspace.id,
            Task.title == "Imported Q2 Research",
        )
    )
    imported_step = session.scalar(
        select(TaskStep).where(
            TaskStep.workspace_id == target_workspace.id,
            TaskStep.work_package_id == "research-1",
        )
    )
    imported_member = session.scalar(
        select(AgentTeamMember).where(
            AgentTeamMember.workspace_id == target_workspace.id,
            AgentTeamMember.team_role == "researcher",
        )
    )
    imported_message = session.scalar(
        select(TaskMessage).where(
            TaskMessage.workspace_id == target_workspace.id,
            TaskMessage.message_type == "step.completed",
        )
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == target_workspace.id,
            AuditEvent.action == "workspace.import.created",
        )
    )
    assert imported_agent is not None
    assert imported_team is not None
    assert imported_member is not None
    assert imported_member.department == "Research"
    assert imported_member.responsibilities == ["collect sources"]
    assert imported_member.skill_weights == {"research": 0.9}
    assert imported_member.max_concurrent_tasks == 2
    assert imported_task is not None
    assert imported_task.team_snapshot == {"team": {"name": "Research Team"}}
    assert imported_task.project_plan == {"work_packages": [{"package_id": "research"}]}
    assert imported_step is not None
    assert imported_step.required_role == "researcher"
    assert imported_step.required_skills == ["research"]
    assert imported_step.expected_artifacts == ["work_summary"]
    assert imported_step.acceptance_criteria == ["Summary is complete."]
    assert imported_step.review_policy == {"reviewer": "manager"}
    assert imported_message is not None
    assert imported_message.task_id == imported_task.id
    assert imported_message.task_step_id == imported_step.id
    assert imported_message.agent_profile_id == imported_agent.id
    assert imported_message.agent_run_id is None
    assert imported_message.sequence == 1
    assert imported_message.body == "Research package completed."
    assert imported_message.payload == {"work_package_id": "research-1"}
    assert imported_task.status == "draft"
    assert audit is not None
    assert audit.user_id == target_user.id


def test_workspace_metadata_import_preview_returns_conflict_plan(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-conflicts@example.com",
        slug="source-conflicts",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-conflicts@example.com",
        slug="target-conflicts",
    )
    source_runtime = RuntimeSpace(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        name="Team Runtime",
        scope="team",
    )
    source_skill = Skill(
        key="source.web-search",
        name="Web Search",
        version="1.0.0",
        capability_keys=["web.search"],
        visibility="public",
    )
    session.add_all([source_runtime, source_skill])
    session.flush()
    source_install = WorkspaceSkillInstall(
        workspace_id=source_workspace.id,
        skill_id=source_skill.id,
        installed_by_user_id=source_user.id,
        installed_key="web-search",
        installed_name="Web Search",
        installed_version="1.0.0",
        installed_capability_keys=["web.search"],
    )
    source_agent = AgentProfile(
        workspace_id=source_workspace.id,
        name="Researcher",
        role="researcher",
        skills={"installed_skill_ids": [str(source_install.id)]},
    )
    source_team = AgentTeam(
        workspace_id=source_workspace.id,
        name="Research Team",
        team_type="research",
        runtime_space_id=source_runtime.id,
    )
    source_task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        title="Q2 Research",
        agent_team_id=source_team.id,
        runtime_space_id=source_runtime.id,
    )
    session.add_all([source_install, source_agent, source_team, source_task])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=source_workspace.id,
                agent_team_id=source_team.id,
                agent_profile_id=source_agent.id,
                team_role="researcher",
            ),
            TaskStep(
                workspace_id=source_workspace.id,
                task_id=source_task.id,
                assigned_agent_profile_id=source_agent.id,
                runtime_space_id=source_runtime.id,
                title="Research",
            ),
            TaskMessage(
                workspace_id=source_workspace.id,
                task_id=source_task.id,
                agent_profile_id=source_agent.id,
                message_type="note",
                sequence=1,
                body="Ready",
            ),
        ]
    )

    target_runtime = RuntimeSpace(
        workspace_id=target_workspace.id,
        created_by_user_id=target_user.id,
        name="Imported Team Runtime",
        scope="team",
    )
    target_skill = Skill(
        key="target.web-search",
        name="Web Search",
        version="1.0.0",
        capability_keys=["web.search"],
        visibility="private",
    )
    session.add_all([target_runtime, target_skill])
    session.flush()
    session.add_all(
        [
            WorkspaceSkillInstall(
                workspace_id=target_workspace.id,
                skill_id=target_skill.id,
                installed_by_user_id=target_user.id,
                installed_key="web-search",
                installed_name="Web Search",
                installed_version="1.0.0",
            ),
            AgentProfile(
                workspace_id=target_workspace.id,
                name="Imported Researcher",
                role="researcher",
            ),
            AgentTeam(
                workspace_id=target_workspace.id,
                name="Imported Research Team",
                team_type="research",
            ),
            Task(
                workspace_id=target_workspace.id,
                created_by_user_id=target_user.id,
                title="Imported Q2 Research",
            ),
        ]
    )
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False, "include_runs": False, "include_files": False},
    )
    export_payload = json.loads(export_response.content)
    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import/preview",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["dry_run"] is True
    assert body["created_counts"]["agents"] == 0
    assert body["skipped_counts"]["agents"] == 1
    assert body["skipped_counts"]["teams"] == 1
    assert body["skipped_counts"]["tasks"] == 1
    assert body["skipped_counts"]["runtime_spaces"] == 1
    assert body["skipped_counts"]["skill_installs"] == 1
    assert body["estimated_counts"]["skip_total"] >= 5
    assert body["estimated_counts"]["required_resolution_total"] == 0
    resources_by_collection = {
        item["collection"]: item for item in body["resources"]
    }
    assert resources_by_collection["agents"]["action"] == "skip"
    assert resources_by_collection["agents"]["source_count"] == 1
    assert resources_by_collection["agents"]["skip_count"] == 1
    assert resources_by_collection["team_members"]["action"] == "skip"
    assert body["required_resolutions"] == []
    conflicts_by_collection = {
        item["collection"]: item for item in body["conflict_plan"]
    }
    assert conflicts_by_collection["agents"]["strategy"] == "skip_existing"
    assert conflicts_by_collection["agents"]["target_value"] == "Imported Researcher"
    assert conflicts_by_collection["teams"]["strategy"] == "skip_existing"
    assert conflicts_by_collection["tasks"]["target_value"] == "Imported Q2 Research"
    assert conflicts_by_collection["runtime_spaces"]["target_value"] == "Imported Team Runtime"
    assert conflicts_by_collection["skill_installs"]["field"] == "installed_key"
    assert "team_members" in conflicts_by_collection
    assert "task_steps" in conflicts_by_collection
    assert "task_messages" in conflicts_by_collection
    assert session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == target_workspace.id,
            AgentProfile.name == "Imported Researcher",
        )
    ) is not None
    assert (
        len(
            session.scalars(
                select(AgentProfile).where(AgentProfile.workspace_id == target_workspace.id)
            ).all()
        )
        == 1
    )


def test_workspace_metadata_import_can_rename_existing_agent_conflict(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-rename@example.com",
        slug="source-rename",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-rename@example.com",
        slug="target-rename",
    )
    source_agent = AgentProfile(
        workspace_id=source_workspace.id,
        name="Researcher",
        role="researcher",
    )
    existing_agent = AgentProfile(
        workspace_id=target_workspace.id,
        name="Imported Researcher",
        role="researcher",
    )
    session.add_all([source_agent, existing_agent])
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={
            "include_teams": False,
            "include_tasks": False,
            "include_runs": False,
            "include_files": False,
            "include_runtime_spaces": False,
            "include_skill_installs": False,
            "include_audit_events": False,
        },
    )
    export_payload = json.loads(export_response.content)
    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import/preview",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "resolutions": {
                f"agents:{source_agent.id}": {
                    "action": "rename",
                    "new_name": "Imported Researcher 2",
                }
            },
        },
    )

    assert export_response.status_code == 200
    assert preview.status_code == 200
    assert preview.json()["required_resolutions"] == []
    assert preview.json()["conflict_plan"][0]["strategy"] == "skip_existing"
    assert preview.json()["conflict_plan"][0]["target_value"] == "Imported Researcher"
    assert preview.json()["suggested_resolutions"] == [
        {
            "collection": "agents",
            "source_id": str(source_agent.id),
            "field": "name",
            "reason": "skip_existing",
            "allowed_actions": ["rename", "skip"],
            "message": "Agent 'Imported Researcher' already exists in target workspace.",
            "resolution_key": f"agents:{source_agent.id}",
        }
    ]
    assert preview.json()["estimated_counts"]["suggested_resolution_total"] == 1
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["agents"] == 1
    assert body["skipped_counts"]["agents"] == 0
    imported = session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == target_workspace.id,
            AgentProfile.name == "Imported Researcher 2",
        )
    )
    assert imported is not None


def test_workspace_metadata_import_preview_rejects_unsupported_format_version(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-format@example.com",
        slug="source-format",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-format@example.com",
        slug="target-format",
    )
    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )
    export_payload = json.loads(export_response.content)
    export_payload["manifest"]["format_version"] = "workspace-export.v999"

    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import/preview",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["created_counts"]["agents"] == 0
    assert body["conflict_plan"] == [
        {
            "collection": "manifest",
            "source_id": export_payload["manifest"]["workspace_id"],
            "field": "format_version",
            "source_value": "workspace-export.v999",
            "target_value": "workspace-export.v1",
            "strategy": "reject",
            "severity": "error",
            "message": (
                "Workspace export format 'workspace-export.v999' is not supported; "
                "expected 'workspace-export.v1'."
            ),
        }
    ]
    assert body["resources"][0]["collection"] == "manifest"
    assert body["resources"][0]["action"] == "requires_resolution"
    assert body["required_resolutions"][0]["allowed_actions"] == [
        "export_supported_version",
        "cancel_import",
    ]


def test_workspace_metadata_export_import_preserves_runtime_spaces_and_skill_snapshots(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-runtime@example.com",
        slug="source-runtime",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-runtime@example.com",
        slug="target-runtime",
    )
    runtime_space = RuntimeSpace(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        name="Design Runtime",
        scope="team",
        policy={"runtime_modes": ["docker"], "resource_requirements": {"cpu": 2}},
        network_policy={"egress": "restricted"},
        storage_policy={"max_gb": 20},
        cleanup_policy={"idle_ttl_minutes": 60},
    )
    skill = Skill(
        key="poster-maker",
        name="Poster Maker",
        version="1.0.0",
        description="Generate posters",
        capability_keys=["image.generate"],
        manifest={"tools": ["generate_image"]},
        visibility="public",
    )
    session.add_all([runtime_space, skill])
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=source_workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="cpu",
        limit_value=4,
        reserved_value=2,
        unit="cores",
    )
    install = WorkspaceSkillInstall(
        workspace_id=source_workspace.id,
        skill_id=skill.id,
        installed_by_user_id=source_user.id,
        installed_key="poster-maker",
        installed_name="Poster Maker",
        installed_version="1.0.0",
        installed_description="Generate posters",
        installed_capability_keys=["image.generate"],
        installed_manifest={"tools": ["generate_image"]},
        source_visibility="public",
        source_checksum="sha256:poster",
        config={"quality": "high"},
    )
    session.add_all([quota, install])
    session.flush()
    agent = AgentProfile(
        workspace_id=source_workspace.id,
        name="Designer",
        role="designer",
        skills={"installed_skill_ids": [str(install.id)]},
    )
    team = AgentTeam(
        workspace_id=source_workspace.id,
        name="Design Team",
        team_type="design",
        runtime_space_id=runtime_space.id,
    )
    session.add_all([agent, team])
    session.flush()
    task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        agent_team_id=team.id,
        runtime_space_id=runtime_space.id,
        title="Poster",
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=source_workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        runtime_space_id=runtime_space.id,
        title="Create poster",
    )
    session.add(step)
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )
    export_payload = json.loads(export_response.content)
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert export_response.status_code == 200
    assert export_payload["manifest"]["counts"]["runtime_spaces"] == 1
    assert export_payload["manifest"]["counts"]["runtime_space_quotas"] == 1
    assert export_payload["manifest"]["counts"]["skill_installs"] == 1
    assert export_payload["teams"][0]["runtime_space_id"] == str(runtime_space.id)
    assert export_payload["tasks"][0]["runtime_space_id"] == str(runtime_space.id)
    assert export_payload["task_steps"][0]["runtime_space_id"] == str(runtime_space.id)
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["runtime_spaces"] == 1
    assert body["created_counts"]["runtime_space_quotas"] == 1
    assert body["created_counts"]["skill_installs"] == 1

    imported_space = session.scalar(
        select(RuntimeSpace).where(
            RuntimeSpace.workspace_id == target_workspace.id,
            RuntimeSpace.name == "Imported Design Runtime",
        )
    )
    imported_agent = session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == target_workspace.id,
            AgentProfile.name == "Imported Designer",
        )
    )
    imported_install = session.scalar(
        select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == target_workspace.id,
            WorkspaceSkillInstall.installed_key == "poster-maker",
        )
    )
    imported_team = session.scalar(
        select(AgentTeam).where(
            AgentTeam.workspace_id == target_workspace.id,
            AgentTeam.name == "Imported Design Team",
        )
    )
    imported_task = session.scalar(
        select(Task).where(
            Task.workspace_id == target_workspace.id,
            Task.title == "Imported Poster",
        )
    )
    imported_step = session.scalar(
        select(TaskStep).where(
            TaskStep.workspace_id == target_workspace.id,
            TaskStep.title == "Create poster",
        )
    )

    assert imported_space is not None
    assert imported_space.policy == {
        "runtime_modes": ["docker"],
        "resource_requirements": {"cpu": 2},
    }
    assert imported_space.network_policy == {"egress": "restricted"}
    assert imported_install is not None
    assert imported_install.installed_manifest == {"tools": ["generate_image"]}
    assert imported_agent is not None
    assert imported_agent.skills == {"installed_skill_ids": [str(imported_install.id)]}
    assert imported_team is not None
    assert imported_team.runtime_space_id == imported_space.id
    assert imported_task is not None
    assert imported_task.runtime_space_id == imported_space.id
    assert imported_step is not None
    assert imported_step.runtime_space_id == imported_space.id


def test_workspace_metadata_import_preview_rejects_disabled_skill_installs(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-disabled-skill@example.com",
        slug="source-disabled-skill",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-disabled-skill@example.com",
        slug="target-disabled-skill",
    )
    skill = Skill(
        key="disabled-poster-maker",
        name="Disabled Poster Maker",
        version="1.0.0",
        capability_keys=["image.generate"],
        manifest={"tools": ["generate_image"]},
        visibility="public",
    )
    session.add(skill)
    session.flush()
    install = WorkspaceSkillInstall(
        workspace_id=source_workspace.id,
        skill_id=skill.id,
        installed_by_user_id=source_user.id,
        installed_key="disabled-poster-maker",
        installed_name="Disabled Poster Maker",
        installed_version="1.0.0",
        installed_capability_keys=["image.generate"],
        installed_manifest={"tools": ["generate_image"]},
        source_visibility="public",
        source_checksum="sha256:disabled",
        status="disabled",
        disabled_at=datetime.now(UTC),
    )
    session.add(install)
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={
            "include_agents": False,
            "include_teams": False,
            "include_tasks": False,
            "include_runs": False,
            "include_files": False,
            "include_runtime_spaces": False,
            "include_audit_events": False,
        },
    )
    export_payload = json.loads(export_response.content)
    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import/preview",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert export_response.status_code == 200
    assert preview.status_code == 200
    body = preview.json()
    assert body["created_counts"]["skill_installs"] == 0
    assert body["skipped_counts"]["skill_installs"] == 1
    assert body["conflict_plan"] == [
        {
            "collection": "skill_installs",
            "source_id": str(install.id),
            "field": "status",
            "source_value": "disabled",
            "target_value": "active",
            "strategy": "reject",
            "severity": "error",
            "message": (
                "Skill install 'disabled-poster-maker' is 'disabled' in the source export; "
                "importing it as active would change the source workspace safety policy."
            ),
        }
    ]
    assert body["required_resolutions"] == [
        {
            "collection": "skill_installs",
            "source_id": str(install.id),
            "field": "status",
            "reason": "reject",
            "allowed_actions": ["exclude_skill", "enable_in_source_and_reexport"],
            "message": body["conflict_plan"][0]["message"],
        }
    ]
    resources_by_collection = {item["collection"]: item for item in body["resources"]}
    assert resources_by_collection["skill_installs"]["action"] == "requires_resolution"
    assert committed.status_code == 200
    assert committed.json()["created_counts"]["skill_installs"] == 0
    assert committed.json()["skipped_counts"]["skill_installs"] == 1
    assert session.scalars(
        select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == target_workspace.id
        )
    ).all() == []


def test_workspace_metadata_import_preview_rejects_runtime_space_without_policy(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-runtime-policy@example.com",
        slug="source-runtime-policy",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-runtime-policy@example.com",
        slug="target-runtime-policy",
    )
    runtime_space = RuntimeSpace(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        name="No Policy Runtime",
        scope="team",
        policy={},
    )
    session.add(runtime_space)
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=source_workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=2,
        unit="count",
    )
    session.add(quota)
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={
            "include_agents": False,
            "include_teams": False,
            "include_tasks": False,
            "include_runs": False,
            "include_files": False,
            "include_skill_installs": False,
            "include_audit_events": False,
        },
    )
    export_payload = json.loads(export_response.content)
    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import/preview",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert export_response.status_code == 200
    assert preview.status_code == 200
    body = preview.json()
    assert body["created_counts"]["runtime_spaces"] == 0
    assert body["skipped_counts"]["runtime_spaces"] == 1
    assert body["skipped_counts"]["runtime_space_quotas"] == 1
    conflict_by_collection = {
        item["collection"]: item for item in body["conflict_plan"]
    }
    assert conflict_by_collection["runtime_spaces"] == {
        "collection": "runtime_spaces",
        "source_id": str(runtime_space.id),
        "field": "policy",
        "source_value": "{}",
        "target_value": None,
        "strategy": "reject",
        "severity": "error",
        "message": (
            "Runtime space 'No Policy Runtime' has no runtime policy in the source export; "
            "import requires an explicit policy before this space can be created."
        ),
    }
    assert body["required_resolutions"] == [
        {
            "collection": "runtime_spaces",
            "source_id": str(runtime_space.id),
            "field": "policy",
            "reason": "reject",
            "allowed_actions": ["add_runtime_policy", "exclude_runtime_space"],
            "message": conflict_by_collection["runtime_spaces"]["message"],
        }
    ]
    assert committed.status_code == 200
    assert committed.json()["created_counts"]["runtime_spaces"] == 0
    assert committed.json()["skipped_counts"]["runtime_spaces"] == 1
    assert session.scalars(
        select(RuntimeSpace).where(RuntimeSpace.workspace_id == target_workspace.id)
    ).all() == []


def test_workspace_metadata_import_preview_rejects_quota_reserved_over_limit(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-quota-violation@example.com",
        slug="source-quota-violation",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-quota-violation@example.com",
        slug="target-quota-violation",
    )
    runtime_space = RuntimeSpace(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        name="Quota Runtime",
        scope="team",
        policy={"runtime_modes": ["docker"]},
    )
    session.add(runtime_space)
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=source_workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=1,
        reserved_value=2,
        unit="count",
    )
    session.add(quota)
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={
            "include_agents": False,
            "include_teams": False,
            "include_tasks": False,
            "include_runs": False,
            "include_files": False,
            "include_skill_installs": False,
            "include_audit_events": False,
        },
    )
    export_payload = json.loads(export_response.content)
    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import/preview",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert export_response.status_code == 200
    assert preview.status_code == 200
    body = preview.json()
    assert body["created_counts"]["runtime_spaces"] == 1
    assert body["created_counts"]["runtime_space_quotas"] == 0
    assert body["skipped_counts"]["runtime_space_quotas"] == 1
    quota_conflict = body["conflict_plan"][0]
    assert quota_conflict == {
        "collection": "runtime_space_quotas",
        "source_id": str(quota.id),
        "field": "reserved_value",
        "source_value": "2",
        "target_value": "1",
        "strategy": "reject",
        "severity": "error",
        "message": (
            "Quota 'active_runs' reserves 2, which exceeds its limit 1; "
            "import requires a consistent quota before commit."
        ),
    }
    assert body["required_resolutions"] == [
        {
            "collection": "runtime_space_quotas",
            "source_id": str(quota.id),
            "field": "reserved_value",
            "reason": "reject",
            "allowed_actions": [
                "increase_quota_limit",
                "release_source_reservations",
                "exclude_quota",
            ],
            "message": quota_conflict["message"],
        }
    ]
    assert committed.status_code == 200
    assert committed.json()["created_counts"]["runtime_spaces"] == 1
    assert committed.json()["created_counts"]["runtime_space_quotas"] == 0
    assert committed.json()["skipped_counts"]["runtime_space_quotas"] == 1
    assert session.scalar(
        select(RuntimeSpace).where(RuntimeSpace.workspace_id == target_workspace.id)
    ) is not None
    assert session.scalars(
        select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.workspace_id == target_workspace.id)
    ).all() == []


def test_workspace_archive_export_includes_metadata_and_file_bytes(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("brief.txt", b"hello archive", "text/plain")},
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "metadata.json" in names
        file_name = f"files/{file_id}/brief.txt"
        assert file_name in names
        assert archive.read(file_name) == b"hello archive"
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["manifest"]["counts"]["files"] == 1


def test_workspace_archive_export_skips_large_objects(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("large.txt", b"1234567890", "text/plain")},
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive",
        headers=_headers(owner.id),
        json={"max_bytes_per_object": 3},
    )

    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert f"files/{file_id}/large.txt" not in names
        assert "skipped-objects.json" in names
        skipped = json.loads(archive.read("skipped-objects.json"))
        assert any("exceeds max_bytes_per_object" in item for item in skipped)


def test_workspace_archive_import_preview_reports_oversized_objects(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-large-import@example.com",
        slug="source-large-import",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-large-import@example.com",
        slug="target-large-import",
    )
    uploaded = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/files",
        headers=_headers(source_user.id),
        files={"file": ("large.txt", b"1234567890", "text/plain")},
    )
    assert uploaded.status_code == 201
    archive_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/archive",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )

    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import/preview",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"max_bytes_per_object": "3"},
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["dry_run"] is True
    assert body["created_counts"]["files"] == 0
    assert body["skipped_counts"]["files"] == 1
    assert body["conflict_plan"][0]["collection"] == "files"
    assert body["conflict_plan"][0]["field"] == "size_bytes"
    assert body["conflict_plan"][0]["strategy"] == "reject"
    assert body["conflict_plan"][0]["severity"] == "error"
    assert body["estimated_counts"]["required_resolution_total"] == 1
    resources_by_collection = {item["collection"]: item for item in body["resources"]}
    assert resources_by_collection["files"]["action"] == "requires_resolution"
    assert resources_by_collection["files"]["required_resolution_count"] == 1
    assert body["required_resolutions"] == [
        {
            "collection": "files",
            "source_id": body["conflict_plan"][0]["source_id"],
            "field": "size_bytes",
            "reason": "reject",
            "allowed_actions": ["increase_max_bytes_per_object", "exclude_object"],
            "message": body["conflict_plan"][0]["message"],
        }
    ]
    assert session.scalars(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    ).all() == []


def test_workspace_archive_import_preview_rejects_checksum_mismatch(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-checksum@example.com",
        slug="source-checksum",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-checksum@example.com",
        slug="target-checksum",
    )
    uploaded = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/files",
        headers=_headers(source_user.id),
        files={"file": ("brief.txt", b"original", "text/plain")},
    )
    archive_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/archive",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )
    tampered = BytesIO()
    with (
        ZipFile(BytesIO(archive_response.content)) as source_zip,
        ZipFile(tampered, mode="w", compression=ZIP_DEFLATED) as target_zip,
    ):
        for name in source_zip.namelist():
            content = source_zip.read(name)
            if name == f"files/{uploaded.json()['id']}/brief.txt":
                content = b"tampered"
            target_zip.writestr(name, content)

    preview = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import/preview",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", tampered.getvalue(), "application/zip")},
    )
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", tampered.getvalue(), "application/zip")},
        data={"dry_run": "false"},
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["created_counts"]["files"] == 0
    assert body["skipped_counts"]["files"] == 1
    assert body["conflict_plan"][0]["collection"] == "files"
    assert body["conflict_plan"][0]["field"] == "checksum_sha256"
    assert body["conflict_plan"][0]["strategy"] == "reject"
    assert body["required_resolutions"] == [
        {
            "collection": "files",
            "source_id": uploaded.json()["id"],
            "field": "checksum_sha256",
            "reason": "reject",
            "allowed_actions": ["replace_archive_object", "exclude_object"],
            "message": body["conflict_plan"][0]["message"],
        }
    ]
    assert committed.status_code == 200
    assert committed.json()["created_counts"]["files"] == 0
    assert committed.json()["skipped_counts"]["files"] == 1
    assert session.scalars(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    ).all() == []


def test_workspace_archive_import_restores_metadata_and_file_bytes(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source@example.com",
        slug="source",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target@example.com",
        slug="target",
    )
    uploaded = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/files",
        headers=_headers(source_user.id),
        files={"file": ("brief.txt", b"portable data", "text/plain")},
    )
    assert uploaded.status_code == 201
    archive_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/archive",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )

    dry_run = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"dry_run": "true"},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["created_counts"]["files"] == 1
    assert session.scalars(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    ).all() == []

    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"dry_run": "false"},
    )
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["files"] == 1
    imported_file = session.scalar(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    )
    assert imported_file is not None
    assert imported_file.filename == "Imported brief.txt"
    downloaded = client.get(
        f"/api/v1/workspaces/{target_workspace.id}/files/{imported_file.id}/download",
        headers=_headers(target_user.id),
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == target_workspace.id,
            AuditEvent.action == "workspace.archive_import.created",
        )
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"portable data"
    assert audit is not None


def test_workspace_archive_import_restores_artifact_bytes_and_task_mapping(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source@example.com",
        slug="source",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target@example.com",
        slug="target",
    )
    source_task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        title="Novel Draft",
        domain_type="writing",
    )
    source_agent = AgentProfile(
        workspace_id=source_workspace.id,
        name="Writer",
        role="writer",
    )
    session.add_all([source_task, source_agent])
    session.flush()
    source_step = TaskStep(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        assigned_agent_profile_id=source_agent.id,
        work_package_id="chapter-1",
        title="Draft chapter",
        status="completed",
    )
    session.add(source_step)
    session.flush()
    artifact_bytes = b"chapter one artifact"
    storage_key = f"workspaces/{source_workspace.id}/artifacts/{source_task.id}/chapter.txt"
    LocalStorage(str(tmp_path)).write(storage_key, artifact_bytes)
    source_artifact = Artifact(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        agent_run_id=None,
        task_step_id=source_step.id,
        agent_profile_id=source_agent.id,
        work_package_id="chapter-1",
        version=2,
        review_status="approved",
        artifact_type="document",
        filename="chapter.txt",
        content_type="text/plain",
        size_bytes=len(artifact_bytes),
        checksum_sha256=sha256(artifact_bytes).hexdigest(),
        storage_key=storage_key,
        artifact_metadata={"stage": "draft"},
        created_at=datetime.now(UTC),
    )
    session.add(source_artifact)
    session.commit()
    archive_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/archive",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )

    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"dry_run": "false"},
    )

    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["tasks"] == 1
    assert body["created_counts"]["artifacts"] == 1
    imported_task = session.scalar(
        select(Task).where(
            Task.workspace_id == target_workspace.id,
            Task.title == "Imported Novel Draft",
        )
    )
    imported_artifact = session.scalar(
        select(Artifact).where(
            Artifact.workspace_id == target_workspace.id,
            Artifact.filename == "Imported chapter.txt",
        )
    )
    assert imported_task is not None
    assert imported_artifact is not None
    assert imported_artifact.task_id == imported_task.id
    assert imported_artifact.agent_run_id is None
    assert imported_artifact.work_package_id == "chapter-1"
    assert imported_artifact.version == 2
    assert imported_artifact.review_status == "approved"
    assert imported_artifact.checksum_sha256 == sha256(artifact_bytes).hexdigest()
    assert imported_artifact.artifact_metadata["imported_from_artifact_id"] == str(
        source_artifact.id
    )
    downloaded = client.get(
        f"/api/v1/workspaces/{target_workspace.id}/artifacts/"
        f"{imported_artifact.id}/download",
        headers=_headers(target_user.id),
    )
    assert downloaded.status_code == 200
    assert downloaded.content == artifact_bytes


def test_workspace_archive_export_job_runs_in_worker_and_downloads_zip(
    tmp_path: Path,
) -> None:
    client, session, session_factory, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("brief.txt", b"async archive", "text/plain")},
    )
    assert uploaded.status_code == 201

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )

    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="export-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
        ),
    )
    assert runner.run_once() is True
    status_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}",
        headers=_headers(owner.id),
    )
    download = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/download",
        headers=_headers(owner.id),
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "completed"
    assert status_body["size_bytes"] > 0
    assert status_body["checksum_sha256"]
    assert download.status_code == 200
    with ZipFile(BytesIO(download.content)) as archive:
        names = set(archive.namelist())
        file_name = f"files/{uploaded.json()['id']}/brief.txt"
        assert "metadata.json" in names
        assert archive.read(file_name) == b"async archive"


def test_workspace_archive_export_job_download_requires_completion(tmp_path: Path) -> None:
    client, session, _, _ = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={"include_file_bytes": False, "include_artifact_bytes": False},
    )
    assert created.status_code == 202

    download = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/"
        f"{created.json()['id']}/download",
        headers=_headers(owner.id),
    )

    assert download.status_code == 409


def _client(tmp_path: Path) -> tuple[TestClient, Session]:
    client, session, _, _ = _client_with_worker_queue(tmp_path)
    return client, session


def _client_with_worker_queue(
    tmp_path: Path,
) -> tuple[TestClient, Session, sessionmaker[Session], RedisQueue]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = session_factory()
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
            max_upload_bytes=1024,
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
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), session, session_factory, queue


def _seed_workspace(session: Session, *, email: str, slug: str) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
