from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.admin.models import PlatformPolicy
from backend.app.admin.policies import RISKY_EXECUTION_POLICY_KEY
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.self_hosted.models import (
    RuntimeCredential,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.self_hosted.service import SelfHostedRuntimeService
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_self_hosted_runtime_registration_and_job_flow() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Local Runtime Space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.commit()

    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "mac-studio"},
    )
    assert enrollment.status_code == 201
    assert enrollment.json()["token"].startswith("ccrt_")

    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "mac-studio",
            "machine_id": "machine-1",
            "version": "0.1.0",
            "capabilities": {"gpu": True, "runtime_space_id": str(runtime_space.id)},
        },
    )
    assert registered.status_code == 201
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    credential = registered.json()["credential_token"]

    heartbeat = client.post(
        "/api/v1/self-hosted/heartbeat",
        headers=_runtime_headers(credential),
        json={
            "status": "online",
            "capabilities": {"gpu": True, "ram_gb": 64, "runtime_space_id": str(runtime_space.id)},
        },
    )
    assert heartbeat.status_code == 200

    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Private render",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        runtime_id=runtime_id,
        input={"prompt": "render locally"},
    )
    session.add(run)
    session.commit()

    next_job = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))
    assert next_job.status_code == 200
    assert next_job.json()["agent_run_id"] == str(run.id)

    claim = client.post(
        f"/api/v1/self-hosted/jobs/{run.id}/claim",
        headers=_runtime_headers(credential),
    )
    assert claim.status_code == 200
    session.refresh(run)
    assert run.status == "running"

    progress = client.post(
        "/api/v1/self-hosted/progress",
        headers=_runtime_headers(credential),
        json={
            "agent_run_id": str(run.id),
            "event_type": "self_hosted.progress",
            "message": "50%",
            "metadata": {"percent": 50},
        },
    )
    assert progress.status_code == 200

    local_file = client.post(
        "/api/v1/self-hosted/local-files",
        headers=_runtime_headers(credential),
        json={"task_id": str(task.id), "path": "/Users/me/data.csv", "label": "private data"},
    )
    assert local_file.status_code == 201
    assert local_file.json()["file_metadata"]["runtime_space_id"] == str(runtime_space.id)
    assert local_file.json()["file_metadata"]["workspace_runtime_id"] == str(runtime_id)

    artifact = client.post(
        "/api/v1/self-hosted/artifact-uploads",
        headers=_runtime_headers(credential),
        json={
            "agent_run_id": str(run.id),
            "filename": "result.png",
            "storage_key": f"workspaces/{workspace.id}/self-hosted/result.png",
            "checksum_sha256": "a" * 64,
        },
    )
    assert artifact.status_code == 201
    assert artifact.json()["artifact_metadata"]["runtime_space_id"] == str(runtime_space.id)
    assert artifact.json()["artifact_metadata"]["workspace_runtime_id"] == str(runtime_id)
    assert session.query(RunEvent).count() == 2

    runtime_credential = session.query(RuntimeCredential).one()
    revoked = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/credentials/{runtime_credential.id}/revoke",
        headers=_headers(owner.id),
    )
    assert revoked.status_code == 204
    denied = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))
    assert denied.status_code == 401
    denied_progress = client.post(
        "/api/v1/self-hosted/progress",
        headers=_runtime_headers(credential),
        json={
            "agent_run_id": str(run.id),
            "event_type": "self_hosted.progress",
            "message": "should not persist",
        },
    )
    denied_local_file = client.post(
        "/api/v1/self-hosted/local-files",
        headers=_runtime_headers(credential),
        json={"task_id": str(task.id), "path": "/Users/me/leak.csv"},
    )
    denied_artifact = client.post(
        "/api/v1/self-hosted/artifact-uploads",
        headers=_runtime_headers(credential),
        json={"agent_run_id": str(run.id), "filename": "late-result.png"},
    )

    assert denied_progress.status_code == 401
    assert denied_local_file.status_code == 401
    assert denied_artifact.status_code == 401


def test_self_hosted_artifact_upload_rejects_cross_workspace_storage_key() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-3",
        },
    )
    credential = registered.json()["credential_token"]

    rejected = client.post(
        "/api/v1/self-hosted/artifact-uploads",
        headers=_runtime_headers(credential),
        json={
            "filename": "../result.png",
            "storage_key": "workspaces/other/self-hosted/result.png",
        },
    )

    assert rejected.status_code == 404


def test_runtime_credentials_are_bound_to_token_hash_pepper() -> None:
    client, session = _client(
        Settings(environment="test", log_format="text", internal_api_token=TOKEN)
    )
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-2",
        },
    )
    credential = registered.json()["credential_token"]

    wrong_settings = Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        token_hash_pepper="different-pepper",
    )
    app = create_app(wrong_settings)

    def override_db_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: wrong_settings
    wrong_client = TestClient(app)

    denied = wrong_client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))

    assert denied.status_code == 401


def test_self_hosted_runtime_respects_platform_policy_disable() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    _seed_risky_policy(session, allow_self_hosted_runtimes=False)

    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "blocked-node"},
    )

    assert enrollment.status_code == 400
    assert "Self-hosted runtimes are disabled" in enrollment.json()["error"]["message"]


def test_self_hosted_worker_cannot_poll_when_policy_is_disabled_after_registration() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-policy",
        },
    )
    credential = registered.json()["credential_token"]
    _seed_risky_policy(session, allow_self_hosted_runtimes=False)

    denied = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))

    assert denied.status_code == 400
    assert "Self-hosted runtimes are disabled" in denied.json()["error"]["message"]


def test_self_hosted_worker_is_limited_to_allowed_runtime_spaces() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    allowed_space = RuntimeSpace(workspace_id=workspace.id, name="Allowed", scope="workspace")
    denied_space = RuntimeSpace(workspace_id=workspace.id, name="Denied", scope="workspace")
    session.add_all([allowed_space, denied_space])
    session.commit()
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-space",
            "capabilities": {
                "runtime_space_id": str(allowed_space.id),
                "allowed_runtime_space_ids": [str(allowed_space.id)],
            },
        },
    )
    credential = registered.json()["credential_token"]
    allowed_run = AgentRun(
        workspace_id=workspace.id,
        runtime_id=UUID(registered.json()["workspace_runtime_id"]),
        runtime_space_id=allowed_space.id,
        status="queued",
        input={"task": "allowed"},
    )
    denied_run = AgentRun(
        workspace_id=workspace.id,
        runtime_id=UUID(registered.json()["workspace_runtime_id"]),
        runtime_space_id=denied_space.id,
        status="queued",
        input={"task": "denied"},
    )
    session.add_all([denied_run, allowed_run])
    session.commit()

    next_job = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))
    denied_claim = client.post(
        f"/api/v1/self-hosted/jobs/{denied_run.id}/claim",
        headers=_runtime_headers(credential),
    )
    allowed_claim = client.post(
        f"/api/v1/self-hosted/jobs/{allowed_run.id}/claim",
        headers=_runtime_headers(credential),
    )

    assert next_job.status_code == 200
    assert next_job.json()["agent_run_id"] == str(allowed_run.id)
    assert denied_claim.status_code == 409
    assert "runtime space is not allowed" in denied_claim.json()["error"]["message"]
    assert allowed_claim.status_code == 200
    runtime = session.get(WorkspaceRuntime, UUID(registered.json()["workspace_runtime_id"]))
    assert runtime is not None
    assert runtime.runtime_space_id == allowed_space.id
    space_events = session.query(RuntimeSpaceEvent).filter_by(runtime_space_id=allowed_space.id)
    assert {event.event_type for event in space_events} >= {
        "self_hosted.registered",
        "self_hosted.job_claimed",
    }


def test_self_hosted_runtime_rejects_foreign_runtime_space_capabilities() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    other_user = User(email="other-runtime-space@example.com", display_name="other")
    other_workspace = Workspace(owner=other_user, name="Other", slug="other-runtime-space")
    other_membership = WorkspaceMember(
        workspace=other_workspace,
        user=other_user,
        role="owner",
    )
    session.add_all([other_user, other_workspace, other_membership])
    session.flush()
    foreign_space = RuntimeSpace(
        workspace_id=other_workspace.id,
        name="Foreign",
        scope="workspace",
    )
    session.add(foreign_space)
    session.commit()
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )

    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-foreign-space",
            "capabilities": {"allowed_runtime_space_ids": [str(foreign_space.id)]},
        },
    )

    assert registered.status_code == 400
    assert "unavailable runtime spaces" in registered.json()["error"]["message"]


def test_self_hosted_heartbeat_cannot_change_runtime_space_binding() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    first_space = RuntimeSpace(workspace_id=workspace.id, name="First", scope="workspace")
    second_space = RuntimeSpace(workspace_id=workspace.id, name="Second", scope="workspace")
    session.add_all([first_space, second_space])
    session.commit()
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-rebind",
            "capabilities": {"runtime_space_id": str(first_space.id)},
        },
    )

    heartbeat = client.post(
        "/api/v1/self-hosted/heartbeat",
        headers=_runtime_headers(registered.json()["credential_token"]),
        json={"status": "online", "capabilities": {"runtime_space_id": str(second_space.id)}},
    )

    assert registered.status_code == 201
    assert heartbeat.status_code == 409
    assert "binding cannot be changed" in heartbeat.json()["error"]["message"]


def test_self_hosted_worker_cleanup_marks_stale_workers_degraded() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Local", scope="workspace")
    session.add(runtime_space)
    session.commit()
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-stale",
            "capabilities": {"runtime_space_id": str(runtime_space.id)},
        },
    )
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    runtime = session.get(WorkspaceRuntime, runtime_id)
    assert runtime is not None
    runtime.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=3_600)
    self_hosted_worker = session.query(SelfHostedWorker).one()
    self_hosted_worker.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=3_600)
    session.commit()

    cleanup = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/worker-cleanup"
        "?stale_after_seconds=60&quarantine_after_seconds=7200",
        headers=_headers(owner.id),
    )

    session.refresh(runtime)
    session.refresh(self_hosted_worker)
    event = session.query(RuntimeSpaceEvent).filter_by(
        runtime_space_id=runtime_space.id,
        event_type="self_hosted.worker_degraded",
    ).one()
    assert cleanup.status_code == 200
    assert cleanup.json()["degraded"] == 1
    assert cleanup.json()["quarantined"] == 0
    assert self_hosted_worker.status == "degraded"
    assert runtime.connection_status == "degraded"
    assert event.event_metadata["runtime_id"] == str(runtime_id)

    recovered = client.post(
        "/api/v1/self-hosted/heartbeat",
        headers=_runtime_headers(registered.json()["credential_token"]),
        json={"status": "online", "capabilities": {"runtime_space_id": str(runtime_space.id)}},
    )

    session.refresh(runtime)
    session.refresh(self_hosted_worker)
    assert recovered.status_code == 200
    assert self_hosted_worker.status == "online"
    assert runtime.connection_status == "online"


def test_self_hosted_worker_cleanup_quarantines_severely_stale_workers() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Local", scope="workspace")
    session.add(runtime_space)
    session.commit()
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-quarantine",
            "capabilities": {"runtime_space_id": str(runtime_space.id)},
        },
    )
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    runtime = session.get(WorkspaceRuntime, runtime_id)
    assert runtime is not None
    self_hosted_worker = session.query(SelfHostedWorker).one()
    last_heartbeat = datetime.now(UTC) - timedelta(seconds=10_000)
    runtime.last_heartbeat_at = last_heartbeat
    self_hosted_worker.last_heartbeat_at = last_heartbeat
    queued_run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="queued")
    session.add(queued_run)
    session.commit()

    cleanup = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/worker-cleanup"
        "?stale_after_seconds=60&quarantine_after_seconds=120",
        headers=_headers(owner.id),
    )
    denied_poll = client.get(
        "/api/v1/self-hosted/jobs/next",
        headers=_runtime_headers(registered.json()["credential_token"]),
    )
    denied_heartbeat = client.post(
        "/api/v1/self-hosted/heartbeat",
        headers=_runtime_headers(registered.json()["credential_token"]),
        json={"status": "online", "capabilities": {"runtime_space_id": str(runtime_space.id)}},
    )

    session.refresh(runtime)
    session.refresh(self_hosted_worker)
    runtime_event = session.query(RuntimeEvent).filter_by(
        workspace_runtime_id=runtime_id,
        event_type="self_hosted.worker_quarantined",
    ).one()
    space_event = session.query(RuntimeSpaceEvent).filter_by(
        runtime_space_id=runtime_space.id,
        event_type="self_hosted.worker_quarantined",
    ).one()
    assert cleanup.status_code == 200
    assert cleanup.json()["degraded"] == 0
    assert cleanup.json()["quarantined"] == 1
    assert self_hosted_worker.status == "quarantined"
    assert runtime.status == "quarantined"
    assert runtime.connection_status == "offline"
    assert denied_poll.status_code == 400
    assert "quarantined" in denied_poll.json()["error"]["message"]
    assert denied_heartbeat.status_code == 409
    assert runtime_event.event_metadata["worker_id"] == str(self_hosted_worker.id)
    assert space_event.event_metadata["runtime_id"] == str(runtime_id)


def test_self_hosted_worker_enforces_max_concurrent_jobs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-capacity",
            "capabilities": {"max_concurrent_jobs": 1},
        },
    )
    credential = registered.json()["credential_token"]
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    first_run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="queued")
    second_run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="queued")
    session.add_all([first_run, second_run])
    session.commit()

    first_claim = client.post(
        f"/api/v1/self-hosted/jobs/{first_run.id}/claim",
        headers=_runtime_headers(credential),
    )
    second_claim = client.post(
        f"/api/v1/self-hosted/jobs/{second_run.id}/claim",
        headers=_runtime_headers(credential),
    )
    next_job = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))

    assert first_claim.status_code == 200
    assert second_claim.status_code == 409
    assert "max concurrent jobs" in second_claim.json()["error"]["message"]
    assert next_job.status_code == 200
    assert next_job.json() is None


def test_self_hosted_worker_enforces_capability_policy_for_jobs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-policy",
            "capabilities": {
                "allowed_tools": ["search_web"],
                "supported_models": ["gpt-4.1-mini"],
                "supported_runtimes": ["self_hosted"],
                "supported_network_modes": ["none"],
            },
        },
    )
    credential = registered.json()["credential_token"]
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    denied_tool_run = _agent_run_with_snapshot(
        workspace_id=workspace.id,
        runtime_id=runtime_id,
        model="gpt-4.1-mini",
        allowed_tools=["runtime_shell"],
        runtime_policy={"provider": "self_hosted", "network": {"mode": "none"}},
    )
    denied_model_run = _agent_run_with_snapshot(
        workspace_id=workspace.id,
        runtime_id=runtime_id,
        model="gpt-5",
        allowed_tools=["search_web"],
        runtime_policy={"provider": "self_hosted", "network": {"mode": "none"}},
    )
    denied_runtime_run = _agent_run_with_snapshot(
        workspace_id=workspace.id,
        runtime_id=runtime_id,
        model="gpt-4.1-mini",
        allowed_tools=["search_web"],
        runtime_policy={"provider": "cloud_docker", "network": {"mode": "none"}},
    )
    denied_network_run = _agent_run_with_snapshot(
        workspace_id=workspace.id,
        runtime_id=runtime_id,
        model="gpt-4.1-mini",
        allowed_tools=["search_web"],
        runtime_policy={"provider": "self_hosted", "network": {"mode": "internet"}},
    )
    allowed_run = _agent_run_with_snapshot(
        workspace_id=workspace.id,
        runtime_id=runtime_id,
        model="gpt-4.1-mini",
        allowed_tools=["search_web"],
        runtime_policy={"provider": "self_hosted", "network": {"mode": "none"}},
    )
    session.add_all(
        [
            denied_tool_run,
            denied_model_run,
            denied_runtime_run,
            denied_network_run,
            allowed_run,
        ]
    )
    session.commit()

    next_job = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))
    denied_tool_claim = client.post(
        f"/api/v1/self-hosted/jobs/{denied_tool_run.id}/claim",
        headers=_runtime_headers(credential),
    )
    denied_model_claim = client.post(
        f"/api/v1/self-hosted/jobs/{denied_model_run.id}/claim",
        headers=_runtime_headers(credential),
    )
    denied_runtime_claim = client.post(
        f"/api/v1/self-hosted/jobs/{denied_runtime_run.id}/claim",
        headers=_runtime_headers(credential),
    )
    denied_network_claim = client.post(
        f"/api/v1/self-hosted/jobs/{denied_network_run.id}/claim",
        headers=_runtime_headers(credential),
    )
    allowed_claim = client.post(
        f"/api/v1/self-hosted/jobs/{allowed_run.id}/claim",
        headers=_runtime_headers(credential),
    )

    assert next_job.status_code == 200
    assert next_job.json()["agent_run_id"] == str(allowed_run.id)
    assert denied_tool_claim.status_code == 409
    assert "requires tools" in denied_tool_claim.json()["error"]["message"]
    assert denied_model_claim.status_code == 409
    assert "model is not supported" in denied_model_claim.json()["error"]["message"]
    assert denied_runtime_claim.status_code == 409
    assert "runtime is not supported" in denied_runtime_claim.json()["error"]["message"]
    assert denied_network_claim.status_code == 409
    assert "network mode is not supported" in denied_network_claim.json()["error"]["message"]
    assert allowed_claim.status_code == 200


def test_self_hosted_mcp_job_poll_claim_and_complete_flow() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-mcp",
            "capabilities": {"allowed_tools": ["generate_image"], "max_concurrent_mcp_jobs": 1},
        },
    )
    credential = registered.json()["credential_token"]
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    server = McpServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": "mcp-image"},
    )
    run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="waiting_runtime")
    session.add_all([server, run])
    session.flush()
    queued_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime_id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        request_payload={"jsonrpc": "2.0", "method": "tools/call"},
    )
    blocked_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime_id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="delete_image",
        request_payload={"jsonrpc": "2.0", "method": "tools/call"},
    )
    session.add_all([queued_job, blocked_job])
    session.commit()

    next_job = client.get("/api/v1/self-hosted/mcp-jobs/next", headers=_runtime_headers(credential))
    claim = client.post(
        f"/api/v1/self-hosted/mcp-jobs/{queued_job.id}/claim",
        headers=_runtime_headers(credential),
    )
    capacity_blocked = client.get(
        "/api/v1/self-hosted/mcp-jobs/next",
        headers=_runtime_headers(credential),
    )
    completed = client.post(
        f"/api/v1/self-hosted/mcp-jobs/{queued_job.id}/complete",
        headers=_runtime_headers(credential),
        json={"status": "completed", "response_payload": {"ok": True}},
    )
    incompatible_claim = client.post(
        f"/api/v1/self-hosted/mcp-jobs/{blocked_job.id}/claim",
        headers=_runtime_headers(credential),
    )
    events = (
        session.query(RunEvent).filter_by(agent_run_id=run.id).order_by(RunEvent.sequence).all()
    )

    assert next_job.status_code == 200
    assert next_job.json()["id"] == str(queued_job.id)
    assert claim.status_code == 200
    assert claim.json()["status"] == "claimed"
    assert capacity_blocked.status_code == 200
    assert capacity_blocked.json() is None
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert incompatible_claim.status_code == 409
    assert "not compatible" in incompatible_claim.json()["error"]["message"]
    session.refresh(queued_job)
    session.refresh(run)
    assert queued_job.status == "completed"
    assert queued_job.response_payload == {"ok": True}
    assert run.status == "queued"
    assert run.input["pending_tool_results"][0]["mcp_job_id"] == str(queued_job.id)
    assert run.input["pending_tool_results"][0]["response"] == {"ok": True}
    assert [event.event_type for event in events] == [
        "self_hosted.mcp_job_claimed",
        "self_hosted.mcp_job_completed",
    ]


def test_self_hosted_mcp_job_poll_skips_incompatible_head_of_queue() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-mcp-skip",
            "capabilities": {"allowed_tools": ["generate_image"]},
        },
    )
    credential = registered.json()["credential_token"]
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    server = McpServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": "mcp-image"},
    )
    run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="waiting_runtime")
    session.add_all([server, run])
    session.flush()
    blocked_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime_id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="delete_image",
        request_payload={"jsonrpc": "2.0", "method": "tools/call"},
    )
    compatible_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime_id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        request_payload={"jsonrpc": "2.0", "method": "tools/call"},
    )
    session.add_all([blocked_job, compatible_job])
    session.commit()

    next_job = client.get("/api/v1/self-hosted/mcp-jobs/next", headers=_runtime_headers(credential))

    assert next_job.status_code == 200
    assert next_job.json()["id"] == str(compatible_job.id)
    assert next_job.json()["tool_name"] == "generate_image"


def test_self_hosted_worker_cleanup_expires_stale_mcp_jobs_idempotently() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-mcp-cleanup",
        },
    )
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    server = McpServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": "mcp-image"},
    )
    run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="waiting_runtime")
    session.add_all([server, run])
    session.flush()
    stale_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime_id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        request_payload={"jsonrpc": "2.0", "method": "tools/call"},
        status="queued",
    )
    fresh_job = SelfHostedMcpJob(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime_id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="search_web",
        request_payload={"jsonrpc": "2.0", "method": "tools/call"},
        status="queued",
    )
    session.add_all([stale_job, fresh_job])
    session.flush()
    stale_job.created_at = datetime.now(UTC) - timedelta(seconds=3_600)
    session.commit()

    cleanup = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/worker-cleanup"
        "?stale_after_seconds=3600&mcp_job_stale_after_seconds=60",
        headers=_headers(owner.id),
    )
    cleanup_again = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/worker-cleanup"
        "?stale_after_seconds=3600&mcp_job_stale_after_seconds=60",
        headers=_headers(owner.id),
    )

    session.refresh(stale_job)
    session.refresh(fresh_job)
    session.refresh(run)
    event = session.query(RunEvent).filter_by(
        agent_run_id=run.id,
        event_type="self_hosted.mcp_job_expired",
    ).one()
    assert cleanup.status_code == 200
    assert cleanup.json()["expired_mcp_jobs"] == 1
    assert cleanup_again.status_code == 200
    assert cleanup_again.json()["expired_mcp_jobs"] == 0
    assert stale_job.status == "expired"
    assert stale_job.error_payload is not None
    assert stale_job.error_payload["code"] == "self_hosted_mcp_job_expired"
    assert fresh_job.status == "queued"
    assert run.status == "failed"
    assert run.error is not None
    assert run.error["code"] == "self_hosted_mcp_job_expired"
    assert run.input["pending_tool_results"][0]["status"] == "expired"
    assert event.event_metadata["mcp_job_id"] == str(stale_job.id)


def test_self_hosted_service_creates_scoped_mcp_job() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-service-mcp",
        },
    )
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    server = McpServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": "mcp-image"},
    )
    run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="running")
    session.add_all([server, run])
    session.commit()

    job = SelfHostedRuntimeService(session, client.app.state.settings).create_mcp_job(
        workspace_id=workspace.id,
        runtime_id=runtime_id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        request_payload={"jsonrpc": "2.0"},
    )
    event = session.query(RunEvent).filter_by(agent_run_id=run.id).one()

    assert job.status == "queued"
    assert job.workspace_runtime_id == runtime_id
    assert event.event_type == "self_hosted.mcp_job_queued"
    assert event.event_metadata["mcp_job_id"] == str(job.id)


def test_self_hosted_artifact_upload_enforces_max_artifact_bytes() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-artifact",
            "capabilities": {"max_artifact_bytes": 1024},
        },
    )
    credential = registered.json()["credential_token"]

    rejected = client.post(
        "/api/v1/self-hosted/artifact-uploads",
        headers=_runtime_headers(credential),
        json={
            "filename": "large.bin",
            "metadata": {"size_bytes": 2048},
        },
    )

    assert rejected.status_code == 404
    assert "Artifact upload exceeds" in rejected.json()["error"]["message"]


def test_self_hosted_revoke_records_runtime_evidence_and_blocks_jobs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Local", scope="workspace")
    session.add(runtime_space)
    session.commit()
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-revoke",
            "capabilities": {"runtime_space_id": str(runtime_space.id)},
        },
    )
    credential_token = registered.json()["credential_token"]
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    run = AgentRun(workspace_id=workspace.id, runtime_id=runtime_id, status="queued")
    session.add(run)
    session.commit()
    claim = client.post(
        f"/api/v1/self-hosted/jobs/{run.id}/claim",
        headers=_runtime_headers(credential_token),
    )
    assert claim.status_code == 200
    credential = session.query(RuntimeCredential).one()

    revoked = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/credentials/{credential.id}/revoke",
        headers=_headers(owner.id),
        json={"reason": "lost laptop"},
    )
    denied = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential_token))

    session.refresh(run)
    runtime = session.get(WorkspaceRuntime, runtime_id)
    worker = session.query(SelfHostedWorker).one()
    claim_record = session.query(SelfHostedJobClaim).one()
    runtime_event = session.query(RuntimeEvent).filter_by(
        event_type="self_hosted.credential_revoked"
    ).one()
    space_event = session.query(RuntimeSpaceEvent).filter_by(
        event_type="self_hosted.credential_revoked"
    ).one()
    assert revoked.status_code == 204
    assert denied.status_code == 401
    assert runtime is not None
    assert runtime.status == "revoked"
    assert runtime.connection_status == "offline"
    assert worker.status == "revoked"
    assert claim_record.status == "revoked"
    assert claim_record.completed_at is not None
    assert run.status == "failed"
    assert run.error is not None
    assert run.error["code"] == "runtime_credential_revoked"
    assert runtime_event.event_metadata["credential_id"] == str(credential.id)
    assert runtime_event.event_metadata["actor_user_id"] == str(owner.id)
    assert runtime_event.event_metadata["reason"] == "lost laptop"
    assert runtime_event.event_metadata["affected_run_ids"] == [str(run.id)]
    assert runtime_event.event_metadata["affected_claim_ids"] == [str(claim_record.id)]
    assert runtime_event.event_metadata["final_heartbeat"]["runtime_status"] == "revoked"
    assert space_event.event_metadata["worker_id"] == str(worker.id)
    assert space_event.event_metadata["affected_run_ids"] == [str(run.id)]


def test_self_hosted_worker_trust_view_summarizes_machine_policy_and_state() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(workspace_id=workspace.id, name="Local", scope="workspace")
    session.add(runtime_space)
    session.commit()
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-trust",
            "version": "0.2.0",
            "capabilities": {
                "runtime_space_id": str(runtime_space.id),
                "allowed_runtime_space_ids": [str(runtime_space.id)],
                "allowed_tools": ["generate_image"],
                "supported_models": ["gpt-4.1-mini"],
                "supported_runtimes": ["self_hosted"],
                "supported_network_modes": ["none"],
                "max_concurrent_jobs": 2,
                "max_concurrent_mcp_jobs": 1,
                "max_artifact_bytes": 4096,
            },
        },
    )
    trust = client.get(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/workers/trust",
        headers=_headers(owner.id),
    )

    assert trust.status_code == 200
    payload = trust.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["worker_id"] == registered.json()["worker_id"]
    assert item["workspace_runtime_id"] == registered.json()["workspace_runtime_id"]
    assert item["runtime_space_id"] == str(runtime_space.id)
    assert item["name"] == "node"
    assert item["machine_id"] == "machine-trust"
    assert item["version"] == "0.2.0"
    assert item["trust_state"] == "active"
    assert item["worker_status"] == "online"
    assert item["runtime_status"] == "active"
    assert item["connection_status"] == "online"
    assert item["credential_status"] == "active"
    assert item["last_heartbeat_at"] is not None
    assert item["credential_last_used_at"] is not None
    assert item["credential_revoked_at"] is None
    assert item["policy_summary"] == {
        "allowed_tools": ["generate_image"],
        "supported_models": ["gpt-4.1-mini"],
        "supported_runtimes": ["self_hosted"],
        "supported_network_modes": ["none"],
        "allowed_runtime_space_ids": [str(runtime_space.id)],
        "max_concurrent_jobs": 2,
        "max_concurrent_mcp_jobs": 1,
        "max_artifact_bytes": 4096,
    }
    assert item["capabilities"] == {
        "runtime_space_id": str(runtime_space.id),
        "allowed_runtime_space_ids": [str(runtime_space.id)],
        "allowed_tools": ["generate_image"],
        "supported_models": ["gpt-4.1-mini"],
        "supported_runtimes": ["self_hosted"],
        "supported_network_modes": ["none"],
        "max_concurrent_jobs": 2,
        "max_concurrent_mcp_jobs": 1,
        "max_artifact_bytes": 4096,
    }


def test_self_hosted_worker_trust_view_reflects_degraded_and_revoked_states() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "degraded-node"},
    )
    first_registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": first.json()["token"],
            "name": "degraded-node",
            "machine_id": "machine-degraded",
        },
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "revoked-node"},
    )
    second_registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": second.json()["token"],
            "name": "revoked-node",
            "machine_id": "machine-revoked",
        },
    )
    degraded_runtime = session.get(
        WorkspaceRuntime,
        UUID(first_registered.json()["workspace_runtime_id"]),
    )
    degraded_worker = (
        session.query(SelfHostedWorker)
        .filter_by(workspace_runtime_id=UUID(first_registered.json()["workspace_runtime_id"]))
        .one()
    )
    assert degraded_runtime is not None
    degraded_worker.status = "degraded"
    degraded_runtime.connection_status = "degraded"
    session.commit()
    credential = (
        session.query(RuntimeCredential)
        .filter_by(workspace_runtime_id=UUID(second_registered.json()["workspace_runtime_id"]))
        .one()
    )
    revoked = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/credentials/{credential.id}/revoke",
        headers=_headers(owner.id),
        json={"reason": "rotated"},
    )

    trust = client.get(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/workers/trust",
        headers=_headers(owner.id),
    )
    states = {item["machine_id"]: item["trust_state"] for item in trust.json()}

    assert revoked.status_code == 204
    assert trust.status_code == 200
    assert states == {
        "machine-degraded": "degraded",
        "machine-revoked": "revoked",
    }


def _client(settings: Settings | None = None) -> tuple[TestClient, Session]:
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
    app_settings = settings or Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
    )
    app = create_app(app_settings)

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    return TestClient(app), session


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="owner")
    workspace = Workspace(owner=user, name="Owner", slug="owner", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _runtime_headers(token: str) -> dict[str, str]:
    return {"X-Runtime-Authorization": f"Bearer {token}"}


def _agent_run_with_snapshot(
    *,
    workspace_id: UUID,
    runtime_id: UUID,
    model: str,
    allowed_tools: list[str],
    runtime_policy: dict[str, object],
) -> AgentRun:
    return AgentRun(
        workspace_id=workspace_id,
        runtime_id=runtime_id,
        status="queued",
        model=model,
        input={
            "authorization_snapshot": {
                "version": 1,
                "workspace_id": str(workspace_id),
                "allowed_tools": allowed_tools,
                "runtime_policy": runtime_policy,
                "model_provider": {"selected_model": model},
            }
        },
    )


def _seed_risky_policy(
    session: Session,
    *,
    allow_self_hosted_runtimes: bool,
) -> PlatformPolicy:
    policy = PlatformPolicy(
        policy_key=RISKY_EXECUTION_POLICY_KEY,
        value={
            "allow_runtime_commands": True,
            "allow_network_egress": False,
            "allow_self_hosted_runtimes": allow_self_hosted_runtimes,
            "require_approval_for_high_risk_tools": True,
        },
        description="test",
    )
    session.add(policy)
    session.commit()
    return policy


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
