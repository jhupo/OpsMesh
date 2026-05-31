import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID
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
from backend.app.exports.models import WorkspaceExportJob
from backend.app.files.models import FileAccessEvent, WorkspaceFile
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


def test_workspace_data_lifecycle_diagnostics_reports_backup_retention_and_audit(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner-lifecycle@example.com", slug="owner")
    _, other_workspace = _seed_workspace(
        session,
        email="other-lifecycle@example.com",
        slug="other",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "schedule": "daily",
                "target_type": "s3",
                "target": {
                    "remote_url": "https://backup.example.test/private",
                    "token": "backup-token",
                },
            },
            "retention": {
                "enabled": True,
                "default_retention_days": 90,
                "file_retention_days": 30,
                "artifact_retention_days": 180,
                "audit_event_retention_days": 365,
                "delete_policy": "manual_review",
            },
        }
    }
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Lifecycle task",
    )
    session.add(task)
    session.flush()
    file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="brief.txt",
        content_type="text/plain",
        size_bytes=20,
        checksum_sha256="a" * 64,
        storage_key="workspaces/owner/files/brief.txt",
    )
    session.add(file)
    session.flush()
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        version=2,
        artifact_type="document",
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=30,
        checksum_sha256="b" * 64,
        storage_key="workspaces/owner/artifacts/report.pdf",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    completed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        request={"include_file_bytes": True, "api_key": "sk-export"},
        storage_key="workspaces/owner/exports/archive.zip",
        filename="archive.zip",
        content_type="application/zip",
        size_bytes=123,
        checksum_sha256="c" * 64,
        completed_at=datetime(2026, 1, 3, tzinfo=UTC),
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
        job_metadata={"token": "job-token", "safe": "ok"},
    )
    failed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="failed",
        request={"headers": {"authorization": "Bearer hidden"}},
        error="network failed",
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    access = FileAccessEvent(
        workspace_id=workspace.id,
        workspace_file_id=file.id,
        user_id=owner.id,
        action="file.download",
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    session.add_all([artifact, completed_job, failed_job, access])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/lifecycle-diagnostics",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/exports/lifecycle-diagnostics",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["export_import"]["format_version"] == "workspace-export.v1"
    assert body["export_import"]["archive_import_supported"] is True
    assert body["export_import"]["latest_export_job"]["status"] == "failed"
    assert body["export_import"]["latest_export_job"]["request"]["headers"] == "[redacted]"
    assert body["export_import"]["latest_successful_archive_export"]["status"] == "completed"
    assert body["export_import"]["latest_successful_archive_export"]["has_storage_object"] is True
    assert body["export_import"]["latest_successful_archive_export"]["request"]["api_key"] == (
        "[redacted]"
    )
    assert body["export_import"]["latest_successful_archive_export"]["metadata"]["token"] == (
        "[redacted]"
    )
    assert body["backup_policy"]["enabled"] is True
    assert body["backup_policy"]["target"]["remote_url"] == "[redacted]"
    assert body["backup_policy"]["target"]["token"] == "[redacted]"
    assert body["backup_policy"]["schedule_status"]["configured"] is True
    assert body["backup_policy"]["schedule_status"]["interval_hours"] == 24
    assert body["backup_policy"]["schedule_status"]["overdue"] is True
    assert "backup_schedule_overdue" in body["backup_policy"]["warnings"]
    assert body["retention_policy"]["enabled"] is True
    assert body["retention_policy"]["file_retention_days"] == 30
    assert body["storage"]["files"]["total_count"] == 1
    assert body["storage"]["artifacts"]["versioned_count"] == 1
    assert body["storage"]["total_bytes"] == 50
    assert body["file_access_audit"]["by_action"] == {"file.download": 1}
    assert body["readiness"]["ready"] is True
    assert body["readiness"]["blocked_reasons"] == []
    assert foreign_response.status_code == 403
    serialized = str(body)
    assert "backup.example.test/private" not in serialized
    assert "backup-token" not in serialized
    assert "sk-export" not in serialized
    assert "job-token" not in serialized
    assert "workspaces/owner/exports/archive.zip" not in serialized


def test_workspace_recovery_readiness_summarizes_restore_health_and_redacts_metadata(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-recovery@example.com",
        slug="owner-recovery",
    )
    _, other_workspace = _seed_workspace(
        session,
        email="other-recovery@example.com",
        slug="other-recovery",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "target_type": "s3",
                "target": {
                    "remote_url": "https://backup.example.test/private",
                    "token": "backup-token",
                },
            },
            "retention": {
                "enabled": True,
                "default_retention_days": 30,
                "delete_policy": "soft_delete",
            },
        }
    }
    completed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        request={"include_file_bytes": True, "api_key": "sk-export"},
        storage_key="workspaces/owner-recovery/exports/archive.zip",
        filename="archive.zip",
        content_type="application/zip",
        size_bytes=123,
        checksum_sha256="c" * 64,
        completed_at=datetime(2026, 1, 3, tzinfo=UTC),
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
        job_metadata={"token": "job-token", "manifest_counts": {"agents": 0}},
    )
    failed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="failed",
        request={"headers": {"authorization": "Bearer hidden"}},
        error="network failed",
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    queued_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="queued",
        request={"token": "queued-token"},
        created_at=datetime(2026, 1, 6, tzinfo=UTC),
    )
    foreign_job = WorkspaceExportJob(
        workspace_id=other_workspace.id,
        export_type="workspace_archive",
        status="completed",
        storage_key="workspaces/other-recovery/exports/archive.zip",
        filename="foreign.zip",
        size_bytes=99,
        checksum_sha256="d" * 64,
        completed_at=datetime(2026, 1, 7, tzinfo=UTC),
        created_at=datetime(2026, 1, 7, tzinfo=UTC),
    )
    import_event = AuditEvent(
        workspace_id=workspace.id,
        actor_type="user",
        actor_id=str(owner.id),
        user_id=owner.id,
        action="workspace.archive_import.created",
        target_type="workspace",
        target_id=str(workspace.id),
        audit_metadata={
            "source_workspace_id": "source-workspace",
            "created_counts": {"files": 1},
            "token": "import-token",
        },
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    session.add_all([completed_job, failed_job, queued_job, foreign_job, import_event])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )
    foreign_response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["latest_successful_archive_export"]["id"] == str(completed_job.id)
    assert body["latest_successful_archive_export"]["has_storage_object"] is True
    assert body["latest_successful_archive_export"]["request"]["api_key"] == "[redacted]"
    assert body["latest_successful_archive_export"]["metadata"]["token"] == "[redacted]"
    assert body["latest_failed_export_job"]["id"] == str(failed_job.id)
    assert body["latest_failed_export_job"]["request"]["headers"] == "[redacted]"
    assert body["latest_archive_import"]["id"] == str(import_event.id)
    assert body["latest_archive_import"]["metadata"]["token"] == "[redacted]"
    assert body["export_jobs"]["total"] == 3
    assert body["export_jobs"]["by_status"] == {
        "completed": 1,
        "failed": 1,
        "queued": 1,
    }
    assert body["export_jobs"]["active_job_count"] == 1
    assert body["export_jobs"]["downloadable_archive_count"] == 1
    assert body["export_jobs"]["latest_job"]["id"] == str(queued_job.id)
    assert body["export_jobs"]["latest_job"]["request"]["token"] == "[redacted]"
    assert body["retention_safety"]["retention_enabled"] is True
    assert body["retention_safety"]["backup_policy_enabled"] is True
    assert body["retention_safety"]["protected_by_successful_archive"] is True
    assert body["restore_readiness"]["ready"] is True
    assert body["restore_readiness"]["blocked_reasons"] == []
    assert body["restore_readiness"]["warnings"] == ["archive_export_jobs_in_progress"]
    assert body["restore_readiness"]["backup_coverage"]["status"] == "verified"
    assert body["restore_readiness"]["backup_coverage"]["score"] == 100
    assert body["restore_readiness"]["backup_coverage"]["uncovered_resource_count"] == 0
    assert body["restore_readiness"]["restore_test_history"]["total_tests"] == 1
    assert body["restore_readiness"]["restore_test_history"][
        "latest_test_covers_latest_archive"
    ] is True
    assert body["restore_readiness"]["restore_test_history"]["latest_created_counts"] == {
        "files": 1
    }
    assert body["restore_readiness"]["downloadable_archive_available"] is True
    assert body["restore_readiness"]["latest_archive_import_test_recorded"] is True
    assert foreign_response.status_code == 403
    serialized = str(body)
    assert "backup.example.test/private" not in serialized
    assert "backup-token" not in serialized
    assert "sk-export" not in serialized
    assert "job-token" not in serialized
    assert "import-token" not in serialized
    assert "queued-token" not in serialized
    assert "Bearer hidden" not in serialized
    assert "workspaces/owner-recovery/exports/archive.zip" not in serialized
    assert "foreign.zip" not in serialized


def test_workspace_recovery_readiness_blocks_stale_archive_backup(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-stale-recovery@example.com",
        slug="owner-stale-recovery",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "target_type": "manual_export",
                "max_archive_age_days": 1,
            },
        }
    }
    completed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        storage_key="workspaces/owner-stale-recovery/exports/archive.zip",
        filename="archive.zip",
        content_type="application/zip",
        size_bytes=123,
        checksum_sha256="c" * 64,
        completed_at=datetime(2026, 1, 3, tzinfo=UTC),
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
        job_metadata={"manifest_counts": {"agents": 0}},
    )
    import_event = AuditEvent(
        workspace_id=workspace.id,
        actor_type="user",
        actor_id=str(owner.id),
        user_id=owner.id,
        action="workspace.archive_import.created",
        target_type="workspace",
        target_id=str(workspace.id),
        audit_metadata={},
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    session.add_all([completed_job, import_event])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["restore_readiness"]["ready"] is False
    assert body["restore_readiness"]["blocked_reasons"] == ["latest_archive_stale"]
    assert body["restore_readiness"]["max_archive_age_days"] == 1
    assert body["restore_readiness"]["latest_archive_age_days"] > 1
    assert body["restore_readiness"]["recommended_actions"] == ["run_archive_export"]


def test_workspace_recovery_readiness_actions_dry_run_and_apply_archive_export(
    tmp_path: Path,
) -> None:
    client, session, _, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-recovery-action@example.com",
        slug="owner-recovery-action",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "max_archive_age_days": 1,
                "archive_request": {
                    "include_audit_events": False,
                    "include_file_bytes": False,
                    "include_artifact_bytes": False,
                },
            }
        }
    }
    stale_completed_at = datetime.now(UTC) - timedelta(days=3)
    session.add(
        WorkspaceExportJob(
            workspace_id=workspace.id,
            created_by_user_id=owner.id,
            export_type="workspace_archive",
            status="completed",
            request={"api_key": "should-redact"},
            job_metadata={"token": "should-redact"},
            storage_key="workspaces/owner-recovery-action/exports/stale.zip",
            filename="stale.zip",
            content_type="application/zip",
            size_bytes=10,
            checksum_sha256="a" * 64,
            completed_at=stale_completed_at,
            created_at=stale_completed_at,
        )
    )
    session.commit()

    dry_run = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness/actions/apply",
        headers=_headers(owner.id),
        json={
            "metadata": {
                "token": "operator-secret",
                "base_url": "https://private.example",
            },
            "reason": "refresh stale backup",
        },
    )

    assert dry_run.status_code == 200
    dry_body = dry_run.json()
    assert dry_body["dry_run"] is True
    assert dry_body["status"] == "dry_run"
    assert dry_body["requested_actions"] == ["run_archive_export"]
    assert dry_body["eligible_action_count"] == 1
    assert dry_body["applied_count"] == 0
    assert dry_body["results"][0]["status"] == "would_apply"
    assert dry_body["summary"]["metadata_keys"] == ["base_url", "token"]
    assert queue.count_queued(workspace_id=workspace.id) == 0
    serialized_dry_run = json.dumps(dry_body)
    assert "operator-secret" not in serialized_dry_run
    assert "https://private.example" not in serialized_dry_run

    applied = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness/actions/apply",
        headers=_headers(owner.id),
        json={
            "dry_run": False,
            "metadata": {
                "token": "operator-secret",
                "base_url": "https://private.example",
            },
            "reason": "refresh stale backup",
        },
    )

    assert applied.status_code == 200
    applied_body = applied.json()
    assert applied_body["status"] == "applied"
    assert applied_body["applied_count"] == 1
    assert applied_body["summary"]["archive_export_jobs_enqueued"] == 1
    assert applied_body["results"][0]["resource_type"] == "workspace_export_job"
    assert queue.count_queued(workspace_id=workspace.id) == 1
    export_job = session.get(WorkspaceExportJob, UUID(applied_body["results"][0]["resource_id"]))
    assert export_job is not None
    assert export_job.status == "queued"
    assert export_job.request["include_audit_events"] is False
    assert export_job.job_metadata["source"] == "recovery_readiness_action"
    assert export_job.job_metadata["metadata_keys"] == ["base_url", "token"]
    serialized_apply = json.dumps(applied_body)
    assert "operator-secret" not in serialized_apply
    assert "https://private.example" not in serialized_apply
    assert "workspaces/owner-recovery-action/exports/stale.zip" not in serialized_apply
    assert (
        session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "workspace.recovery_readiness.actions_applied",
                AuditEvent.workspace_id == workspace.id,
            )
        )
        is not None
    )
    assert (
        session.scalar(
            select(AuditEvent).where(
                AuditEvent.action
                == "workspace.recovery_readiness.archive_export_enqueued",
                AuditEvent.workspace_id == workspace.id,
            )
        )
        is not None
    )


def test_workspace_recovery_readiness_actions_skip_when_export_active(
    tmp_path: Path,
) -> None:
    client, session, _, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-recovery-active@example.com",
        slug="owner-recovery-active",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "archive_request": {"include_audit_events": False},
            }
        }
    }
    session.add(
        WorkspaceExportJob(
            workspace_id=workspace.id,
            created_by_user_id=owner.id,
            export_type="workspace_archive",
            status="queued",
            request={"include_audit_events": False},
        )
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness/actions/apply",
        headers=_headers(owner.id),
        json={"dry_run": False, "actions": ["run_archive_export"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "noop"
    assert body["eligible_action_count"] == 0
    assert body["applied_count"] == 0
    assert body["skipped_count"] == 1
    assert body["skipped"][0]["reason"] == "archive_export_already_active"
    assert body["summary"]["active_archive_export_job_count"] == 1
    assert queue.count_queued(workspace_id=workspace.id) == 0


def test_workspace_recovery_readiness_warns_when_backup_schedule_is_overdue(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-overdue-schedule@example.com",
        slug="owner-overdue-schedule",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "schedule": "daily",
                "target_type": "manual_export",
            },
        }
    }
    now = datetime.now(UTC)
    completed_at = now - timedelta(hours=49)
    completed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        storage_key="workspaces/owner-overdue-schedule/exports/archive.zip",
        filename="archive.zip",
        content_type="application/zip",
        size_bytes=123,
        checksum_sha256="c" * 64,
        completed_at=completed_at,
        created_at=completed_at,
        job_metadata={"manifest_counts": {"agents": 0}},
    )
    import_event = AuditEvent(
        workspace_id=workspace.id,
        actor_type="user",
        actor_id=str(owner.id),
        user_id=owner.id,
        action="workspace.archive_import.created",
        target_type="workspace",
        target_id=str(workspace.id),
        audit_metadata={},
        created_at=now - timedelta(hours=48),
    )
    session.add_all([completed_job, import_event])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert "backup_schedule_overdue" in body["retention_safety"]["warnings"]
    assert body["restore_readiness"]["ready"] is True
    assert body["restore_readiness"]["blocked_reasons"] == []
    assert body["restore_readiness"]["warnings"] == ["backup_schedule_overdue"]
    assert body["restore_readiness"]["recommended_actions"] == ["run_archive_export"]


def test_workspace_recovery_readiness_requires_restore_test_after_latest_archive(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-restore-history@example.com",
        slug="owner-restore-history",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "target_type": "manual_export",
            },
        }
    }
    completed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        storage_key="workspaces/owner-restore-history/exports/archive.zip",
        filename="archive.zip",
        content_type="application/zip",
        size_bytes=123,
        checksum_sha256="c" * 64,
        completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
        job_metadata={"manifest_counts": {"agents": 0}},
    )
    older_import_event = AuditEvent(
        workspace_id=workspace.id,
        actor_type="user",
        actor_id=str(owner.id),
        user_id=owner.id,
        action="workspace.archive_import.created",
        target_type="workspace",
        target_id=str(workspace.id),
        audit_metadata={
            "source_workspace_id": "source-workspace",
            "created_counts": {"agents": 0},
            "skipped_counts": {"agents": 0},
            "token": "restore-token",
        },
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
    )
    session.add_all([completed_job, older_import_event])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    history = body["restore_readiness"]["restore_test_history"]
    assert body["restore_readiness"]["ready"] is False
    assert body["restore_readiness"]["blocked_reasons"] == [
        "restore_test_older_than_latest_archive"
    ]
    assert body["restore_readiness"]["recommended_actions"] == ["run_restore_import_test"]
    assert history["total_tests"] == 1
    assert history["latest_test_covers_latest_archive"] is False
    assert history["tests_after_latest_archive"] == 0
    assert history["latest_created_counts"] == {"agents": 0}
    assert "restore-token" not in str(body)


def test_workspace_recovery_readiness_reports_backup_coverage_gap(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-coverage-recovery@example.com",
        slug="owner-coverage-recovery",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "target_type": "manual_export",
                "max_archive_age_days": 30,
            },
        }
    }
    agent = AgentProfile(workspace_id=workspace.id, name="New Agent", role="researcher")
    completed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        storage_key="workspaces/owner-coverage-recovery/exports/archive.zip",
        filename="archive.zip",
        content_type="application/zip",
        size_bytes=123,
        checksum_sha256="c" * 64,
        completed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        job_metadata={"manifest_counts": {"agents": 0}},
    )
    import_event = AuditEvent(
        workspace_id=workspace.id,
        actor_type="user",
        actor_id=str(owner.id),
        user_id=owner.id,
        action="workspace.archive_import.created",
        target_type="workspace",
        target_id=str(workspace.id),
        audit_metadata={},
        created_at=datetime.now(UTC),
    )
    session.add_all([agent, completed_job, import_event])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    coverage = body["restore_readiness"]["backup_coverage"]
    assert body["restore_readiness"]["ready"] is False
    assert body["restore_readiness"]["blocked_reasons"] == ["backup_coverage_incomplete"]
    assert body["restore_readiness"]["recommended_actions"] == ["run_archive_export"]
    assert coverage["status"] == "partial"
    assert coverage["score"] == 0
    assert coverage["current_counts"]["agents"] == 1
    assert coverage["archived_counts"]["agents"] == 0
    assert coverage["uncovered_counts"] == {"agents": 1}
    assert coverage["uncovered_resource_count"] == 1


def test_workspace_retention_preview_reports_scoped_candidates_and_redacts_metadata(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-retention-preview@example.com",
        slug="owner-retention-preview",
    )
    _, other_workspace = _seed_workspace(
        session,
        email="other-retention-preview@example.com",
        slug="other-retention-preview",
    )
    workspace.settings = _retention_settings()
    old_at = datetime.now(UTC) - timedelta(days=45)
    fresh_at = datetime.now(UTC) - timedelta(days=3)
    old_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="old.txt",
        content_type="text/plain",
        size_bytes=12,
        checksum_sha256="1" * 64,
        storage_key="workspaces/owner-retention-preview/files/secret-old.txt",
        created_at=old_at,
    )
    fresh_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="fresh.txt",
        content_type="text/plain",
        size_bytes=8,
        checksum_sha256="2" * 64,
        storage_key="workspaces/owner-retention-preview/files/fresh.txt",
        created_at=fresh_at,
    )
    foreign_file = WorkspaceFile(
        workspace_id=other_workspace.id,
        uploaded_by_user_id=None,
        filename="foreign.txt",
        content_type="text/plain",
        size_bytes=9,
        checksum_sha256="3" * 64,
        storage_key="workspaces/other/files/foreign.txt",
        created_at=old_at,
    )
    old_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        request={"token": "job-secret"},
        storage_key="workspaces/owner-retention-preview/exports/old.zip",
        filename="old.zip",
        content_type="application/zip",
        size_bytes=30,
        checksum_sha256="4" * 64,
        completed_at=old_at,
        created_at=old_at,
        job_metadata={"api_key": "sk-hidden"},
    )
    backup_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        request={},
        storage_key="workspaces/owner-retention-preview/exports/latest.zip",
        filename="latest.zip",
        content_type="application/zip",
        size_bytes=40,
        checksum_sha256="5" * 64,
        completed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="Retention task")
    session.add_all([old_file, fresh_file, foreign_file, old_job, backup_job, task])
    session.flush()
    old_artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        artifact_type="document",
        filename="old-artifact.txt",
        content_type="text/plain",
        size_bytes=20,
        checksum_sha256="6" * 64,
        storage_key="workspaces/owner-retention-preview/artifacts/old.txt",
        created_at=old_at,
    )
    session.add(old_artifact)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/retention/preview",
        headers=_headers(owner.id),
        json={},
    )
    foreign_response = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/exports/retention/preview",
        headers=_headers(owner.id),
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["applied"] is False
    assert body["blocked_reasons"] == []
    assert body["warnings"] == ["manual_review_candidates_present"]
    assert body["recommended_actions"] == [
        "review_retention_candidates",
        "apply_retention",
    ]
    assert body["counts"] == {"files": 1, "export_jobs": 1, "artifacts": 1, "total": 3}
    candidates_by_type = {candidate["resource_type"]: candidate for candidate in body["candidates"]}
    assert candidates_by_type["file"]["resource_id"] == str(old_file.id)
    assert candidates_by_type["file"]["action"] == "soft_delete"
    assert candidates_by_type["export_job"]["resource_id"] == str(old_job.id)
    assert candidates_by_type["export_job"]["action"] == "manual_review"
    assert candidates_by_type["artifact"]["resource_id"] == str(old_artifact.id)
    assert candidates_by_type["artifact"]["action"] == "manual_review"
    assert foreign_response.status_code == 403
    serialized = str(body)
    assert str(fresh_file.id) not in serialized
    assert str(foreign_file.id) not in serialized
    assert "secret-old.txt" not in serialized
    assert "job-secret" not in serialized
    assert "sk-hidden" not in serialized


def test_workspace_retention_apply_soft_deletes_files_and_records_audit(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-retention-apply@example.com",
        slug="owner-retention-apply",
    )
    workspace.settings = _retention_settings()
    old_at = datetime.now(UTC) - timedelta(days=40)
    old_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="old.txt",
        content_type="text/plain",
        size_bytes=12,
        checksum_sha256="7" * 64,
        storage_key="workspaces/owner-retention-apply/files/old.txt",
        created_at=old_at,
    )
    backup_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        request={},
        storage_key="workspaces/owner-retention-apply/exports/latest.zip",
        filename="latest.zip",
        content_type="application/zip",
        size_bytes=40,
        checksum_sha256="8" * 64,
        completed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    session.add_all([old_file, backup_job])
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/retention/apply",
        headers=_headers(owner.id),
        json={"include_export_jobs": False, "include_artifacts": False},
    )
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/files", headers=_headers(owner.id))
    downloaded = client.get(
        f"/api/v1/workspaces/{workspace.id}/files/{old_file.id}/download",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False
    assert body["applied"] is True
    assert body["recommended_actions"] == []
    assert body["applied_counts"] == {"files": 1, "export_jobs": 0, "artifacts": 0}
    session.refresh(old_file)
    assert old_file.status == "retention_deleted"
    assert old_file.file_metadata["retention_delete_policy"] == "soft_delete"
    assert listed.status_code == 200
    assert listed.json()["total"] == 0
    assert downloaded.status_code == 404
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.retention_applied",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["applied_counts"]["files"] == 1


def test_workspace_retention_apply_blocks_without_successful_backup(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-retention-blocked@example.com",
        slug="owner-retention-blocked",
    )
    workspace.settings = _retention_settings()
    old_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="old.txt",
        content_type="text/plain",
        size_bytes=12,
        checksum_sha256="9" * 64,
        storage_key="workspaces/owner-retention-blocked/files/old.txt",
        created_at=datetime.now(UTC) - timedelta(days=40),
    )
    session.add(old_file)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/retention/apply",
        headers=_headers(owner.id),
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is False
    assert body["blocked_reasons"] == ["no_successful_archive_export"]
    assert body["recommended_actions"] == ["run_archive_export"]
    assert body["candidates"] == []
    session.refresh(old_file)
    assert old_file.status == "active"


def test_workspace_retention_disabled_policy_is_reported_as_blocked(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-retention-disabled@example.com",
        slug="owner-retention-disabled",
    )
    workspace.settings = {"data_lifecycle": {"retention": {"enabled": False}}}
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/retention/preview",
        headers=_headers(owner.id),
        json={"require_successful_backup": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is False
    assert body["blocked_reasons"] == ["retention_policy_not_enabled"]
    assert body["recommended_actions"] == ["enable_retention_policy"]
    assert body["candidates"] == []


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
    dependency_suggestion = next(
        item
        for item in body["suggested_resolutions"]
        if item["collection"] == "team_members"
    )
    team_member_conflict = conflicts_by_collection["team_members"]
    assert dependency_suggestion["recommended_action"] == "import_dependency"
    assert dependency_suggestion["resolution_template"] == {
        "action": "import_dependency",
        "dependency_field": team_member_conflict["field"],
        "dependency_id": team_member_conflict["source_value"],
    }
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
    preview_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == target_workspace.id,
            AuditEvent.action == "workspace.import.previewed",
        )
    )
    readiness = client.get(
        f"/api/v1/workspaces/{target_workspace.id}/exports/recovery-readiness",
        headers=_headers(target_user.id),
    )
    foreign_readiness = client.get(
        f"/api/v1/workspaces/{target_workspace.id}/exports/recovery-readiness",
        headers=_headers(source_user.id),
    )
    assert preview_audit is not None
    assert preview_audit.audit_metadata["dry_run"] is True
    assert preview_audit.audit_metadata["conflict_counts"]["agents"] == 1
    assert preview_audit.audit_metadata["conflict_strategy_counts"]["skip_existing"] >= 1
    assert preview_audit.audit_metadata["required_resolution_count"] == 0
    serialized_audit = str(preview_audit.audit_metadata)
    assert "Imported Researcher" not in serialized_audit
    assert "Imported Team Runtime" not in serialized_audit
    assert "web-search" not in serialized_audit
    assert readiness.status_code == 200
    conflict_history = readiness.json()["restore_readiness"]["import_conflict_history"]
    assert conflict_history["total_previews"] == 1
    assert conflict_history["conflict_counts"]["agents"] == 1
    assert conflict_history["conflict_strategy_counts"]["skip_existing"] >= 1
    assert conflict_history["recent_previews"][0]["action"] == "workspace.import.previewed"
    assert foreign_readiness.status_code == 403
    serialized_readiness = str(conflict_history)
    assert "Imported Researcher" not in serialized_readiness
    assert "Imported Team Runtime" not in serialized_readiness
    assert "web-search" not in serialized_readiness


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
            "recommended_action": "rename",
            "resolution_template": {
                "action": "rename",
                "new_name": "Imported Researcher 2",
            },
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


def test_workspace_metadata_import_can_map_existing_team_member_dependencies(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-existing-deps@example.com",
        slug="source-existing-deps",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-existing-deps@example.com",
        slug="target-existing-deps",
    )
    source_agent = AgentProfile(
        workspace_id=source_workspace.id,
        name="Researcher",
        role="researcher",
    )
    source_team = AgentTeam(
        workspace_id=source_workspace.id,
        name="Research Team",
        team_type="research",
    )
    session.add_all([source_agent, source_team])
    session.flush()
    source_member = AgentTeamMember(
        workspace_id=source_workspace.id,
        agent_team_id=source_team.id,
        agent_profile_id=source_agent.id,
        team_role="researcher",
    )
    target_agent = AgentProfile(
        workspace_id=target_workspace.id,
        name="Imported Researcher",
        role="researcher",
    )
    target_team = AgentTeam(
        workspace_id=target_workspace.id,
        name="Imported Research Team",
        team_type="research",
    )
    session.add_all([source_member, target_agent, target_team])
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={
            "include_tasks": False,
            "include_runs": False,
            "include_files": False,
            "include_runtime_spaces": False,
            "include_skill_installs": False,
            "include_audit_events": False,
        },
    )
    export_payload = json.loads(export_response.content)
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "resolutions": {
                f"team_members:{source_member.id}": {
                    "action": "import_dependency",
                    "dependencies": {
                        "agent_team_id": str(target_team.id),
                        "agent_profile_id": str(target_agent.id),
                    },
                }
            },
        },
    )

    assert export_response.status_code == 200
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["agents"] == 0
    assert body["created_counts"]["teams"] == 0
    assert body["created_counts"]["team_members"] == 1
    assert body["skipped_counts"]["agents"] == 1
    assert body["skipped_counts"]["teams"] == 1
    assert body["required_resolutions"] == []
    imported_member = session.scalar(
        select(AgentTeamMember).where(
            AgentTeamMember.workspace_id == target_workspace.id,
            AgentTeamMember.agent_team_id == target_team.id,
            AgentTeamMember.agent_profile_id == target_agent.id,
        )
    )
    assert imported_member is not None


def test_workspace_metadata_import_can_map_existing_task_dependencies(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-existing-task-deps@example.com",
        slug="source-existing-task-deps",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-existing-task-deps@example.com",
        slug="target-existing-task-deps",
    )
    source_task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        title="Q2 Research",
    )
    session.add(source_task)
    session.flush()
    source_step = TaskStep(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        title="Research",
    )
    source_message = TaskMessage(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        message_type="note",
        sequence=1,
        body="Ready",
    )
    target_task = Task(
        workspace_id=target_workspace.id,
        created_by_user_id=target_user.id,
        title="Imported Q2 Research",
    )
    session.add_all([source_step, source_message, target_task])
    session.commit()

    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={
            "include_agents": False,
            "include_teams": False,
            "include_runs": False,
            "include_files": False,
            "include_runtime_spaces": False,
            "include_skill_installs": False,
            "include_audit_events": False,
        },
    )
    export_payload = json.loads(export_response.content)
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "resolutions": {
                f"task_steps:{source_step.id}": {
                    "action": "import_dependency",
                    "dependencies": {"task_id": str(target_task.id)},
                },
                f"task_messages:{source_message.id}": {
                    "action": "import_dependency",
                    "dependencies": {"task_id": str(target_task.id)},
                },
            },
        },
    )

    assert export_response.status_code == 200
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["tasks"] == 0
    assert body["created_counts"]["task_steps"] == 1
    assert body["created_counts"]["task_messages"] == 1
    assert body["skipped_counts"]["tasks"] == 1
    assert body["required_resolutions"] == []
    imported_step = session.scalar(
        select(TaskStep).where(
            TaskStep.workspace_id == target_workspace.id,
            TaskStep.task_id == target_task.id,
        )
    )
    imported_message = session.scalar(
        select(TaskMessage).where(
            TaskMessage.workspace_id == target_workspace.id,
            TaskMessage.task_id == target_task.id,
        )
    )
    assert imported_step is not None
    assert imported_message is not None


def test_workspace_metadata_import_accepts_matching_preview_token(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-preview-token@example.com",
        slug="source-preview-token",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-preview-token@example.com",
        slug="target-preview-token",
    )
    source_agent = AgentProfile(
        workspace_id=source_workspace.id,
        name="Researcher",
        role="researcher",
    )
    session.add(source_agent)
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
        json={"export": export_payload},
    )
    preview_body = preview.json()
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "preview_token": preview_body["preview_token"],
        },
    )

    assert export_response.status_code == 200
    assert preview.status_code == 200
    assert preview_body["preview_token"]
    assert preview_body["dry_run"] is True
    assert committed.status_code == 200
    body = committed.json()
    assert body["preview_token"] == preview_body["preview_token"]
    assert body["created_counts"]["agents"] == 1
    assert body["required_resolutions"] == []
    imported = session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == target_workspace.id,
            AgentProfile.name == "Imported Researcher",
        )
    )
    assert imported is not None


def test_workspace_metadata_import_rejects_mismatched_preview_token(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-bad-preview-token@example.com",
        slug="source-bad-preview-token",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-bad-preview-token@example.com",
        slug="target-bad-preview-token",
    )
    session.add(
        AgentProfile(
            workspace_id=source_workspace.id,
            name="Researcher",
            role="researcher",
        )
    )
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
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "preview_token": "stale-preview-token",
        },
    )

    assert export_response.status_code == 200
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["agents"] == 0
    assert body["skipped_counts"]["agents"] == 0
    assert body["conflict_plan"] == [
        {
            "collection": "manifest",
            "source_id": str(source_workspace.id),
            "field": "preview_token",
            "source_value": "stale-preview-token",
            "target_value": None,
            "strategy": "reject",
            "severity": "error",
            "message": "Import preview token does not match the supplied metadata payload.",
        }
    ]
    assert body["required_resolutions"] == [
        {
            "collection": "manifest",
            "source_id": str(source_workspace.id),
            "field": "preview_token",
            "reason": "reject",
            "allowed_actions": ["rerun_preview", "commit_without_token"],
            "message": "Import preview token does not match the supplied metadata payload.",
        }
    ]
    assert body["resources"][0]["action"] == "requires_resolution"
    assert body["estimated_counts"]["required_resolution_total"] == 1
    assert session.scalar(
        select(AgentProfile).where(AgentProfile.workspace_id == target_workspace.id)
    ) is None


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


def test_workspace_metadata_import_can_exclude_disabled_skill_resolution(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-exclude-disabled-skill@example.com",
        slug="source-exclude-disabled-skill",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-exclude-disabled-skill@example.com",
        slug="target-exclude-disabled-skill",
    )
    skill = Skill(
        key="disabled-chart-maker",
        name="Disabled Chart Maker",
        version="1.0.0",
        capability_keys=["chart.generate"],
        manifest={"tools": ["generate_chart"]},
        visibility="public",
    )
    session.add(skill)
    session.flush()
    install = WorkspaceSkillInstall(
        workspace_id=source_workspace.id,
        skill_id=skill.id,
        installed_by_user_id=source_user.id,
        installed_key="disabled-chart-maker",
        installed_name="Disabled Chart Maker",
        installed_version="1.0.0",
        installed_capability_keys=["chart.generate"],
        installed_manifest={"tools": ["generate_chart"]},
        source_visibility="public",
        source_checksum="sha256:disabled-chart",
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
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "resolutions": {
                f"skill_installs:{install.id}": {"action": "exclude_skill"}
            },
        },
    )

    assert export_response.status_code == 200
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["skill_installs"] == 0
    assert body["skipped_counts"]["skill_installs"] == 1
    assert body["required_resolutions"] == []
    assert body["conflict_plan"] == []
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
    assert body["suggested_resolutions"][0] == {
        "collection": "runtime_spaces",
        "source_id": str(runtime_space.id),
        "field": "policy",
        "reason": "reject",
        "allowed_actions": ["add_runtime_policy", "exclude_runtime_space"],
        "message": conflict_by_collection["runtime_spaces"]["message"],
        "resolution_key": f"runtime_spaces:{runtime_space.id}",
        "recommended_action": "add_runtime_policy",
        "resolution_template": {
            "action": "add_runtime_policy",
            "policy": {"runtime_modes": ["docker"], "network": "restricted"},
        },
    }
    assert committed.status_code == 200
    assert committed.json()["created_counts"]["runtime_spaces"] == 0
    assert committed.json()["skipped_counts"]["runtime_spaces"] == 1
    assert session.scalars(
        select(RuntimeSpace).where(RuntimeSpace.workspace_id == target_workspace.id)
    ).all() == []


def test_workspace_metadata_import_can_add_missing_runtime_policy_resolution(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-runtime-policy-resolution@example.com",
        slug="source-runtime-policy-resolution",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-runtime-policy-resolution@example.com",
        slug="target-runtime-policy-resolution",
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
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "resolutions": {
                f"runtime_spaces:{runtime_space.id}": {
                    "action": "add_runtime_policy",
                    "policy": {"runtime_modes": ["docker"], "network": "restricted"},
                }
            },
        },
    )

    assert export_response.status_code == 200
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["runtime_spaces"] == 1
    assert body["created_counts"]["runtime_space_quotas"] == 1
    assert body["required_resolutions"] == []
    imported_runtime = session.scalar(
        select(RuntimeSpace).where(RuntimeSpace.workspace_id == target_workspace.id)
    )
    assert imported_runtime is not None
    assert imported_runtime.policy == {"runtime_modes": ["docker"], "network": "restricted"}
    imported_quota = session.scalar(
        select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.workspace_id == target_workspace.id)
    )
    assert imported_quota is not None
    assert imported_quota.limit_value == 2
    assert imported_quota.reserved_value == 0


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


def test_workspace_metadata_import_can_release_source_quota_reservations(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-quota-release@example.com",
        slug="source-quota-release",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-quota-release@example.com",
        slug="target-quota-release",
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
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "resolutions": {
                f"runtime_space_quotas:{quota.id}": {
                    "action": "release_source_reservations"
                }
            },
        },
    )

    assert export_response.status_code == 200
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["runtime_spaces"] == 1
    assert body["created_counts"]["runtime_space_quotas"] == 1
    assert body["required_resolutions"] == []
    imported_quota = session.scalar(
        select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.workspace_id == target_workspace.id)
    )
    assert imported_quota is not None
    assert imported_quota.limit_value == 1
    assert imported_quota.reserved_value == 0


def test_workspace_metadata_import_can_increase_quota_limit_resolution(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-quota-increase@example.com",
        slug="source-quota-increase",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-quota-increase@example.com",
        slug="target-quota-increase",
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
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={
            "export": export_payload,
            "dry_run": False,
            "resolutions": {
                f"runtime_space_quotas:{quota.id}": {
                    "action": "increase_quota_limit",
                    "limit_value": 3,
                }
            },
        },
    )

    assert export_response.status_code == 200
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["runtime_spaces"] == 1
    assert body["created_counts"]["runtime_space_quotas"] == 1
    assert body["required_resolutions"] == []
    imported_quota = session.scalar(
        select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.workspace_id == target_workspace.id)
    )
    assert imported_quota is not None
    assert imported_quota.limit_value == 3
    assert imported_quota.reserved_value == 0


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
        assert "storage_key" not in metadata["files"][0]


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
    assert body["suggested_resolutions"] == [
        {
            "collection": "files",
            "source_id": uploaded.json()["id"],
            "field": "checksum_sha256",
            "reason": "reject",
            "allowed_actions": ["replace_archive_object", "exclude_object"],
            "message": body["conflict_plan"][0]["message"],
            "resolution_key": f"files:{uploaded.json()['id']}",
            "recommended_action": "replace_archive_object",
            "resolution_template": {"action": "replace_archive_object"},
        }
    ]
    assert committed.status_code == 200
    assert committed.json()["created_counts"]["files"] == 0
    assert committed.json()["skipped_counts"]["files"] == 1
    preview_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == target_workspace.id,
            AuditEvent.action == "workspace.archive_import.previewed",
        )
    )
    readiness = client.get(
        f"/api/v1/workspaces/{target_workspace.id}/exports/recovery-readiness",
        headers=_headers(target_user.id),
    )
    assert preview_audit is not None
    assert preview_audit.audit_metadata["conflict_counts"] == {"files": 1}
    assert preview_audit.audit_metadata["conflict_severity_counts"] == {"error": 1}
    assert preview_audit.audit_metadata["conflict_strategy_counts"] == {"reject": 1}
    assert preview_audit.audit_metadata["required_resolution_count"] == 1
    assert "original" not in str(preview_audit.audit_metadata)
    assert "tampered" not in str(preview_audit.audit_metadata)
    assert readiness.status_code == 200
    conflict_history = readiness.json()["restore_readiness"]["import_conflict_history"]
    assert conflict_history["total_previews"] == 1
    assert conflict_history["total_conflicts"] == 1
    assert conflict_history["required_resolution_count"] == 1
    assert conflict_history["conflict_counts"] == {"files": 1}
    assert conflict_history["conflict_severity_counts"] == {"error": 1}
    assert conflict_history["conflict_strategy_counts"] == {"reject": 1}
    assert conflict_history["recent_previews"][0]["action"] == (
        "workspace.archive_import.previewed"
    )
    assert "resolve_import_conflicts_before_restore" in readiness.json()[
        "restore_readiness"
    ]["recommended_actions"]
    assert "original" not in str(conflict_history)
    assert "tampered" not in str(conflict_history)
    assert session.scalars(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    ).all() == []


def test_workspace_archive_import_can_replace_checksum_mismatch_with_resolution(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-replace-checksum@example.com",
        slug="source-replace-checksum",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-replace-checksum@example.com",
        slug="target-replace-checksum",
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
                content = b"replacement"
            target_zip.writestr(name, content)

    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", tampered.getvalue(), "application/zip")},
        data={
            "dry_run": "false",
            "resolutions": json.dumps(
                {
                    f"files:{uploaded.json()['id']}": {
                        "action": "replace_archive_object"
                    }
                }
            ),
        },
    )

    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["files"] == 1
    assert body["skipped_counts"]["files"] == 0
    assert body["required_resolutions"] == []
    imported_file = session.scalar(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    )
    assert imported_file is not None
    assert imported_file.checksum_sha256 == sha256(b"replacement").hexdigest()


def test_workspace_archive_import_rejects_invalid_resolution_json(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source-invalid-archive-resolution@example.com",
        slug="source-invalid-archive-resolution",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target-invalid-archive-resolution@example.com",
        slug="target-invalid-archive-resolution",
    )
    archive_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/archive",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )

    response = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"dry_run": "false", "resolutions": "{not json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Archive import resolutions must be valid JSON"


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
    with ZipFile(BytesIO(archive_response.content)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert "storage_key" not in metadata["artifacts"][0]

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
    assert "storage_key" not in created.json()
    assert created.json()["has_storage_object"] is False
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
    export_job = session.get(WorkspaceExportJob, UUID(job_id))
    assert export_job is not None
    export_job.job_metadata = {
        **export_job.job_metadata,
        "api_key": "sk-export",
        "nested": {"base_url": "https://export.example.test/private"},
    }
    session.commit()
    status_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}",
        headers=_headers(owner.id),
    )
    download = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/download",
        headers=_headers(owner.id),
    )
    verified = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/verify",
        headers=_headers(owner.id),
    )
    readiness = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "completed"
    assert status_body["size_bytes"] > 0
    assert status_body["checksum_sha256"]
    assert "storage_key" not in status_body
    assert status_body["has_storage_object"] is True
    assert status_body["job_metadata"]["api_key"] == "[redacted]"
    assert status_body["job_metadata"]["nested"]["base_url"] == "[redacted]"
    assert "sk-export" not in str(status_body)
    assert "export.example.test/private" not in str(status_body)
    assert download.status_code == 200
    with ZipFile(BytesIO(download.content)) as archive:
        names = set(archive.namelist())
        file_name = f"files/{uploaded.json()['id']}/brief.txt"
        assert "metadata.json" in names
        assert archive.read(file_name) == b"async archive"
    assert verified.status_code == 200
    verify_body = verified.json()
    assert verify_body["verified"] is True
    assert verify_body["failed_checks"] == []
    assert verify_body["checks"]["checksum_matches"] is True
    assert verify_body["checks"]["manifest_valid"] is True
    assert "storage_key" not in str(verify_body)
    assert readiness.status_code == 200
    integrity = readiness.json()["archive_integrity"]
    assert integrity["latest_check_verified"] is True
    assert integrity["latest_check_covers_latest_successful_archive"] is True
    assert integrity["latest_check"]["target_id"] == job_id


def test_workspace_archive_export_job_verify_reports_tampered_archive(
    tmp_path: Path,
) -> None:
    client, session, session_factory, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-tampered-archive@example.com",
        slug="owner-tampered-archive",
    )
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="verify-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
        ),
    )
    assert runner.run_once() is True
    export_job = session.get(WorkspaceExportJob, UUID(job_id))
    assert export_job is not None
    assert export_job.storage_key is not None
    LocalStorage(str(tmp_path)).write(export_job.storage_key, b"tampered archive")

    verified = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/verify",
        headers=_headers(owner.id),
    )

    assert verified.status_code == 200
    body = verified.json()
    assert body["verified"] is False
    assert "checksum_matches" in body["failed_checks"]
    assert "zip_readable" in body["failed_checks"]
    assert body["checks"]["metadata_present"] is False
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.archive_export_job.integrity_checked",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["verified"] is False
    assert "tampered archive" not in str(body)


def test_workspace_archive_restore_drill_is_recorded_in_recovery_readiness(
    tmp_path: Path,
) -> None:
    client, session, session_factory, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-restore-drill@example.com",
        slug="owner-restore-drill",
    )
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="drill-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
        ),
    )
    assert runner.run_once() is True

    drill = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/restore-drill",
        headers=_headers(owner.id),
        json={},
    )
    assert drill.status_code == 200
    drill_body = drill.json()
    assert drill_body["workspace_id"] == str(workspace.id)
    assert drill_body["job_id"] == job_id
    assert drill_body["import_preview"]["dry_run"] is True
    assert drill_body["import_preview"]["source_workspace_id"] == str(workspace.id)
    assert drill_body["passed"] == (drill_body["required_resolution_count"] == 0)
    assert "storage_key" not in str(drill_body)

    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.archive_restore_drill.completed",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["source_export_job_id"] == job_id
    assert audit.audit_metadata["passed"] == drill_body["passed"]

    readiness = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/recovery-readiness",
        headers=_headers(owner.id),
    )
    assert readiness.status_code == 200
    body = readiness.json()
    history = body["restore_readiness"]["restore_test_history"]
    assert body["latest_restore_drill"]["action"] == "workspace.archive_restore_drill.completed"
    assert body["latest_restore_drill"]["target_id"] == job_id
    assert body["restore_readiness"]["latest_archive_import_test_recorded"] is True
    assert body["restore_readiness"]["latest_archive_import_tested_at"] is not None
    assert history["latest_test_covers_latest_archive"] is True
    assert history["recent_tests"][0]["action"] == "workspace.archive_restore_drill.completed"
    assert "storage_key" not in str(body)


def test_worker_maintenance_enqueues_due_workspace_backup_job(tmp_path: Path) -> None:
    client, session, session_factory, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-scheduled-backup@example.com",
        slug="owner-scheduled-backup",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {
                "enabled": True,
                "schedule": "daily",
                "target_type": "manual_export",
                "archive_request": {
                    "include_audit_events": False,
                    "include_file_bytes": False,
                    "include_artifact_bytes": False,
                },
            },
        }
    }
    session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="lifecycle-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
        ),
    )

    maintenance = runner.run_maintenance()

    assert maintenance.lifecycle_backup_jobs_enqueued == 1
    assert queue.count_queued(workspace_id=workspace.id) == 1
    queued_job = queue.peek(limit=1)[0]
    assert queued_job.workspace_id == workspace.id
    assert queued_job.job_type == "workspace.archive_export"
    export_job = session.get(WorkspaceExportJob, queued_job.resource_id)
    assert export_job is not None
    assert export_job.status == "queued"
    assert export_job.request["include_audit_events"] is False
    assert export_job.job_metadata["scheduled_by"] == "workspace_data_lifecycle"
    export_job.job_metadata = {**export_job.job_metadata, "token": "scheduled-secret"}
    session.commit()
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.lifecycle.backup_enqueued",
        )
    )
    assert audit is not None
    assert audit.user_id == owner.id
    assert audit.audit_metadata["reason"] == "backup_schedule_due"
    second_maintenance = runner.run_maintenance()
    assert second_maintenance.lifecycle_backup_jobs_skipped == 1
    diagnostics = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/lifecycle-diagnostics",
        headers=_headers(owner.id),
    )
    assert diagnostics.status_code == 200
    automation = diagnostics.json()["automation"]["scheduled_backup"]
    assert automation["due"] is True
    assert automation["blocked_by_active_export"] is True
    assert automation["active_archive_export_job_count"] == 1
    assert automation["latest_event"]["action"] == "workspace.lifecycle.backup_skipped"
    assert automation["latest_event"]["metadata"]["reason"] == "archive_export_already_active"
    assert {
        event["action"] for event in automation["recent_events"]
    } == {
        "workspace.lifecycle.backup_enqueued",
        "workspace.lifecycle.backup_skipped",
    }
    assert automation["latest_scheduled_archive_export_job"]["id"] == str(export_job.id)
    assert automation["latest_scheduled_archive_export_job"]["metadata"]["token"] == "[redacted]"
    assert "storage_key" not in str(automation)
    assert "scheduled-secret" not in str(automation)


def test_worker_maintenance_applies_due_workspace_retention(tmp_path: Path) -> None:
    client, session, session_factory, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-scheduled-retention@example.com",
        slug="owner-scheduled-retention",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {"enabled": True, "target_type": "manual_export"},
            "retention": {
                "enabled": True,
                "auto_apply": True,
                "schedule": "daily",
                "file_retention_days": 30,
                "delete_policy": "soft_delete",
            },
        }
    }
    old_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="old.txt",
        content_type="text/plain",
        size_bytes=12,
        checksum_sha256="1" * 64,
        storage_key="workspaces/owner-scheduled-retention/files/old.txt",
        created_at=datetime.now(UTC) - timedelta(days=45),
    )
    completed_job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        export_type="workspace_archive",
        status="completed",
        storage_key="workspaces/owner-scheduled-retention/exports/latest.zip",
        filename="latest.zip",
        content_type="application/zip",
        size_bytes=50,
        checksum_sha256="2" * 64,
        completed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    session.add_all([old_file, completed_job])
    session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="retention-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
        ),
    )

    maintenance = runner.run_maintenance()

    session.refresh(old_file)
    assert maintenance.lifecycle_retention_runs_applied == 1
    assert old_file.status == "retention_deleted"
    assert old_file.file_metadata["retention_delete_policy"] == "soft_delete"
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.retention_applied",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["applied_counts"]["files"] == 1
    diagnostics = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/lifecycle-diagnostics",
        headers=_headers(owner.id),
    )
    assert diagnostics.status_code == 200
    automation = diagnostics.json()["automation"]["scheduled_retention"]
    assert automation["auto_apply"] is True
    assert automation["configured"] is True
    assert automation["interval_hours"] == 24
    assert automation["latest_run_at"] is not None
    assert automation["next_due_at"] is not None
    assert automation["due"] is False
    assert automation["latest_event"]["action"] == "workspace.retention_applied"


def test_worker_maintenance_runs_due_workspace_restore_drill(tmp_path: Path) -> None:
    client, session, session_factory, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(
        session,
        email="owner-scheduled-restore-drill@example.com",
        slug="owner-scheduled-restore-drill",
    )
    workspace.settings = {
        "data_lifecycle": {
            "backup": {"enabled": True, "target_type": "manual_export"},
            "restore_drill": {
                "enabled": True,
                "schedule": "daily",
                "request": {
                    "import_file_bytes": False,
                    "import_artifact_bytes": False,
                },
            },
        }
    }
    session.commit()
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={
            "include_audit_events": False,
            "include_file_bytes": False,
            "include_artifact_bytes": False,
        },
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="restore-drill-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
        ),
    )
    assert runner.run_once() is True

    maintenance = runner.run_maintenance()

    assert maintenance.lifecycle_restore_drills_completed == 1
    drill_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.archive_restore_drill.completed",
        )
    )
    lifecycle_audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.lifecycle.restore_drill_completed",
        )
    )
    assert drill_audit is not None
    assert drill_audit.audit_metadata["source_export_job_id"] == job_id
    assert lifecycle_audit is not None
    assert lifecycle_audit.audit_metadata["source_export_job_id"] == job_id

    diagnostics = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/lifecycle-diagnostics",
        headers=_headers(owner.id),
    )
    assert diagnostics.status_code == 200
    automation = diagnostics.json()["automation"]["scheduled_restore_drill"]
    assert automation["enabled"] is True
    assert automation["configured"] is True
    assert automation["interval_hours"] == 24
    assert automation["latest_successful_archive_export_job_id"] == job_id
    assert automation["latest_run_at"] is not None
    assert automation["next_due_at"] is not None
    assert automation["due"] is False
    assert automation["latest_event"]["action"] == "workspace.lifecycle.restore_drill_completed"
    assert "storage_key" not in str(automation)

    second_maintenance = runner.run_maintenance()
    assert second_maintenance.lifecycle_restore_drills_completed == 0


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


def _retention_settings() -> dict[str, object]:
    return {
        "data_lifecycle": {
            "backup": {"enabled": True, "target_type": "manual_export"},
            "retention": {
                "enabled": True,
                "default_retention_days": 30,
                "file_retention_days": 30,
                "export_job_retention_days": 30,
                "artifact_retention_days": 30,
                "delete_policy": "soft_delete",
            },
        }
    }


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
