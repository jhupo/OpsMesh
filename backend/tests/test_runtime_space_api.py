from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_runtime_space_api_lifecycle_and_workspace_scope() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
        json={
            "name": "Research runtime space",
            "scope": "workspace",
            "policy": {
                "resource_requirements": {"memory_mb": 1024},
                "api_key": "sk-space",
            },
            "network_policy": {"mode": "none"},
            "storage_policy": {"remote_url": "https://storage.example.test/private"},
            "cleanup_policy": {"headers": {"authorization": "Bearer cleanup"}},
            "quota_limits": {"active_runs": 2, "memory_mb": 2048},
        },
    )

    assert created.status_code == 201
    runtime_space_id = UUID(created.json()["id"])
    assert created.json()["workspace_id"] == str(workspace.id)
    assert created.json()["created_by_user_id"] == str(owner.id)
    assert created.json()["network_policy"] == {"mode": "none"}
    assert created.json()["policy"]["api_key"] == "[redacted]"
    assert created.json()["storage_policy"]["remote_url"] == "[redacted]"
    assert created.json()["cleanup_policy"]["headers"] == "[redacted]"
    assert "sk-space" not in str(created.json())
    assert "storage.example.test/private" not in str(created.json())

    forbidden_read = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/runtime-spaces/{runtime_space_id}",
        headers=_headers(other_owner.id),
    )
    assert forbidden_read.status_code == 404

    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
    )
    events = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space_id}/events",
        headers=_headers(owner.id),
    )
    updated = client.patch(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space_id}",
        headers=_headers(owner.id),
        json={"status": "paused", "quota_limits": {"active_runs": 1}},
    )
    reset = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space_id}/reset",
        headers=_headers(owner.id),
    )

    quotas = session.scalars(
        select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.runtime_space_id == runtime_space_id)
    ).all()
    bindings = session.scalars(
        select(RuntimeSpaceBinding).where(
            RuntimeSpaceBinding.runtime_space_id == runtime_space_id,
        )
    ).all()

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert events.status_code == 200
    assert events.json()["total"] == 1
    assert updated.status_code == 200
    assert updated.json()["status"] == "paused"
    assert reset.status_code == 200
    assert {quota.quota_key: quota.limit_value for quota in quotas} == {
        "active_runs": 1,
        "memory_mb": 2048,
    }
    assert {quota.quota_key: quota.status for quota in quotas} == {
        "active_runs": "active",
        "memory_mb": "disabled",
    }
    assert len(bindings) == 1
    assert bindings[0].target_type == "workspace"
    assert bindings[0].target_id == workspace.id
    stored_events = session.scalars(
        select(RuntimeSpaceEvent)
        .where(RuntimeSpaceEvent.runtime_space_id == runtime_space_id)
        .order_by(RuntimeSpaceEvent.created_at.asc())
    ).all()
    assert [event.event_type for event in stored_events] == [
        "runtime_space.created",
        "runtime_space.status_updated",
        "runtime_space.reset_requested",
    ]
    assert stored_events[1].event_metadata["before_status"] == "active"
    assert stored_events[1].event_metadata["after_status"] == "paused"


def test_team_runtime_space_flows_to_task_and_initial_run() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")

    runtime_space = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
        json={"name": "Design team space", "scope": "workspace"},
    )
    runtime_space_id = UUID(runtime_space.json()["id"])
    manager = client.post(
        f"/api/v1/workspaces/{workspace.id}/agents",
        headers=_headers(owner.id),
        json={"name": "PM", "role": "project_manager"},
    )

    team = client.post(
        f"/api/v1/workspaces/{workspace.id}/teams",
        headers=_headers(owner.id),
        json={
            "name": "Design Team",
            "team_type": "design",
            "runtime_space_id": str(runtime_space_id),
            "manager_agent_profile_id": manager.json()["id"],
        },
    )
    task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Create landing mock", "agent_team_id": team.json()["id"]},
    )

    stored_team = session.get(AgentTeam, UUID(team.json()["id"]))
    stored_runs = session.scalars(
        select(AgentRun).where(AgentRun.workspace_id == workspace.id)
    ).all()

    assert manager.status_code == 201
    assert team.status_code == 201
    assert team.json()["runtime_space_id"] == str(runtime_space_id)
    assert task.status_code == 201
    assert task.json()["runtime_space_id"] == str(runtime_space_id)
    assert stored_team is not None
    assert stored_team.runtime_space_id == runtime_space_id
    assert len(stored_runs) == 1
    assert stored_runs[0].runtime_space_id == runtime_space_id


def test_runtime_space_rejects_cross_workspace_team_binding() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    other_owner, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other-space",
    )
    other_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Other Team",
        team_type="research",
    )
    session.add(other_team)
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(owner.id),
        json={
            "name": "Invalid team space",
            "scope": "team",
            "target_id": str(other_team.id),
        },
    )
    forbidden = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/runtime-spaces",
        headers=_headers(other_owner.id),
        json={
            "name": "Valid other team space",
            "scope": "team",
            "target_id": str(other_team.id),
        },
    )

    assert response.status_code == 400
    assert "Team not found" in response.json()["error"]["message"]
    assert forbidden.status_code == 201


def test_runtime_space_pause_and_resume_clears_blocked_steps() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    viewer = User(email="viewer@example.com", display_name="Viewer")
    session.add(viewer)
    session.add(WorkspaceMember(workspace=workspace, user=viewer, role="viewer"))
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        title="Blocked task",
        status="queued",
        runtime_space_id=runtime_space.id,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Blocked step",
        status="queued",
        runtime_space_id=runtime_space.id,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "runtime_space_paused",
            "priority_score": 8,
        },
    )
    session.add(step)
    session.commit()

    paused = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space.id}/pause",
        headers=_headers(owner.id),
        json={"reason": "maintenance"},
    )
    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space.id}/resume",
        headers=_headers(viewer.id),
    )
    resumed = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space.id}/resume",
        headers=_headers(owner.id),
    )

    session.refresh(runtime_space)
    session.refresh(step)
    events = session.scalars(
        select(RuntimeSpaceEvent)
        .where(RuntimeSpaceEvent.runtime_space_id == runtime_space.id)
        .order_by(RuntimeSpaceEvent.created_at.asc())
    ).all()

    assert paused.status_code == 200
    assert paused.json()["runtime_space"]["status"] == "paused"
    assert paused.json()["cleared_blocked_steps"] == 0
    assert denied.status_code == 403
    assert resumed.status_code == 200
    assert resumed.json()["runtime_space"]["status"] == "active"
    assert resumed.json()["cleared_blocked_steps"] == 1
    assert runtime_space.status == "active"
    assert step.dependencies == {}
    assert [event.event_type for event in events] == [
        "runtime_space.paused",
        "runtime_space.resumed",
    ]
    assert events[0].event_metadata["reason"] == "maintenance"
    assert events[1].event_metadata["cleared_blocked_steps"] == 1


def test_runtime_space_force_release_reservations_updates_quota_and_events() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Quota space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=3,
        reserved_value=3,
    )
    reservation = RuntimeSpaceReservation(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        reservation_key="task:owned",
        resource_usage={"active_runs": 2},
        status="active",
    )
    other_reservation = RuntimeSpaceReservation(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        reservation_key="task:other",
        resource_usage={"active_runs": 1},
        status="active",
    )
    task = Task(
        workspace_id=workspace.id,
        title="Blocked by quota",
        status="queued",
        runtime_space_id=runtime_space.id,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Quota step",
        status="queued",
        runtime_space_id=runtime_space.id,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "runtime_space_quota_exceeded:active_runs",
            "blocked_resource_keys": ["active_runs"],
            "priority_score": 10,
        },
    )
    session.add_all([quota, reservation, other_reservation, step])
    session.commit()

    released = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/"
        f"{runtime_space.id}/reservations/force-release",
        headers=_headers(owner.id),
        json={"reservation_key": "task:owned", "reason": "stale worker lease"},
    )

    session.refresh(quota)
    session.refresh(reservation)
    session.refresh(other_reservation)
    session.refresh(step)
    events = session.scalars(
        select(RuntimeSpaceEvent)
        .where(RuntimeSpaceEvent.runtime_space_id == runtime_space.id)
        .order_by(RuntimeSpaceEvent.created_at.asc())
    ).all()

    assert released.status_code == 200
    assert released.json()["released_reservations"] == 1
    assert released.json()["cleared_blocked_steps"] == 1
    assert released.json()["runtime_space"]["status"] == "active"
    assert quota.reserved_value == 1
    assert reservation.status == "released"
    assert reservation.released_at is not None
    assert other_reservation.status == "active"
    assert "scheduling_status" not in step.dependencies
    assert "blocked_reason" not in step.dependencies
    assert "blocked_resource_keys" not in step.dependencies
    assert [event.event_type for event in events] == [
        "runtime_space.reservations_force_released"
    ]
    assert events[0].event_metadata["released_reservations"] == 1
    assert events[0].event_metadata["cleared_blocked_steps"] == 1
    assert events[0].event_metadata["reason"] == "stale worker lease"


def test_runtime_space_reset_releases_reservations_and_clears_blocks() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Reset space",
        scope="workspace",
        status="paused",
    )
    session.add(runtime_space)
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="runtime",
        status="running",
        connection_status="online",
    )
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="memory_mb",
        limit_value=4096,
        reserved_value=2048,
        unit="mb",
    )
    task = Task(
        workspace_id=workspace.id,
        title="Blocked reset task",
        status="queued",
        runtime_space_id=runtime_space.id,
    )
    session.add_all([runtime, quota, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Reset step",
        status="queued",
        runtime_space_id=runtime_space.id,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "runtime_space_unavailable",
            "priority_score": 20,
        },
    )
    reservation = RuntimeSpaceReservation(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        task_id=task.id,
        task_step_id=step.id,
        reservation_key="task:reset",
        resource_usage={"memory_mb": 2048},
        status="active",
    )
    session.add_all([step, reservation])
    session.commit()

    reset = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space.id}/reset",
        headers=_headers(owner.id),
    )

    session.refresh(runtime_space)
    session.refresh(quota)
    session.refresh(reservation)
    session.refresh(step)
    event = session.query(RuntimeSpaceEvent).filter_by(
        runtime_space_id=runtime_space.id,
        event_type="runtime_space.reset_requested",
    ).one()
    assert reset.status_code == 200
    assert reset.json()["runtime_space"]["status"] == "active"
    assert reset.json()["released_reservations"] == 1
    assert reset.json()["cleared_blocked_steps"] == 1
    assert reset.json()["affected_runtimes"] == 1
    assert runtime_space.status == "active"
    assert quota.reserved_value == 0
    assert reservation.status == "released"
    assert "scheduling_status" not in step.dependencies
    assert event.event_metadata["before_status"] == "paused"
    assert event.event_metadata["after_status"] == "active"
    assert event.event_metadata["released_reservations"] == 1
    assert event.event_metadata["cleared_blocked_steps"] == 1
    assert event.event_metadata["affected_runtime_ids"] == [str(runtime.id)]


def test_runtime_space_diagnostics_reports_quota_reservations_runtimes_and_blocks() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Diagnostics space",
        scope="workspace",
        policy={"api_key": "sk-space"},
    )
    session.add(runtime_space)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        title="Blocked task",
        status="queued",
        runtime_space_id=runtime_space.id,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Blocked step",
        status="queued",
        runtime_space_id=runtime_space.id,
        dependencies={
            "scheduling_status": "blocked",
            "blocked_reason": "runtime_space_quota_exceeded:memory_mb",
            "blocked_resource_keys": ["memory_mb"],
            "priority_score": 12,
        },
    )
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="runtime",
        status="running",
        connection_status="online",
        docker_container_id="container-secret",
        limits={"memory_mb": 4096, "token": "runtime-token"},
        network_policy={"base_url": "https://runtime.example.test/private"},
        capabilities={"headers": {"authorization": "Bearer hidden"}, "tools": ["python"]},
    )
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="memory_mb",
        limit_value=4096,
        reserved_value=4096,
        unit="mb",
    )
    reservation = RuntimeSpaceReservation(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        task_id=task.id,
        task_step_id=step.id,
        reservation_key="task:blocked",
        resource_usage={"memory_mb": 4096},
        status="active",
    )
    session.add_all([step, runtime, quota, reservation])
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space.id}/diagnostics",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_space"]["policy"]["api_key"] == "[redacted]"
    assert payload["quotas"] == [
        {
            "quota_key": "memory_mb",
            "limit_value": 4096,
            "reserved_value": 4096,
            "unit": "mb",
            "utilization": 1.0,
            "saturated": True,
        }
    ]
    assert payload["active_reservations"][0]["reservation_key"] == "task:blocked"
    assert payload["active_reservations"][0]["resource_usage"] == {"memory_mb": 4096}
    assert payload["runtimes"][0]["has_docker_container"] is True
    assert "container-secret" not in str(payload)
    assert payload["runtimes"][0]["limits"]["token"] == "[redacted]"
    assert payload["runtimes"][0]["network_policy"]["base_url"] == "[redacted]"
    assert payload["runtimes"][0]["capabilities"]["headers"] == "[redacted]"
    assert payload["blocked_steps"][0]["task_step_id"] == str(step.id)
    assert payload["blocked_steps"][0]["code"] == "runtime_space_quota_exceeded"
    assert payload["blocked_steps"][0]["resource_key"] == "memory_mb"
    assert payload["blocked_steps"][0]["blocked_resource_keys"] == ["memory_mb"]


def test_runtime_space_events_redact_sensitive_metadata() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner")
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team space",
        scope="workspace",
    )
    session.add(runtime_space)
    session.flush()
    session.add(
        RuntimeSpaceEvent(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            event_type="runtime.cleanup",
            message="cleanup",
            event_metadata={
                "container_id": "container-secret",
                "nested": {"base_url": "https://runtime.example.test/private"},
                "safe": "visible",
            },
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces/{runtime_space.id}/events",
        headers=_headers(owner.id),
    )

    assert response.status_code == 200
    metadata = response.json()["items"][0]["event_metadata"]
    assert metadata == {
        "container_id": "[redacted]",
        "nested": {"base_url": "[redacted]"},
        "safe": "visible",
    }
    assert "container-secret" not in str(metadata)
    assert "runtime.example.test/private" not in str(metadata)


def test_viewer_cannot_manage_runtime_spaces() -> None:
    client, session = _client()
    viewer, workspace = _seed_workspace(session, role="viewer")

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/runtime-spaces",
        headers=_headers(viewer.id),
        json={"name": "Viewer space"},
    )

    assert response.status_code == 403
    assert session.query(RuntimeSpace).count() == 0
    assert session.query(RuntimeSpaceEvent).count() == 0


def _client() -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_session = session_factory()
    settings = Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        database_url="sqlite+pysqlite:///:memory:",
    )
    app = create_app(settings)
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis_client, RedisKeyBuilder(settings.redis_key_prefix), "agent.run")

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), seed_session


def _seed_workspace(
    session: Session,
    *,
    role: str,
    email: str = "owner@example.com",
    slug: str = "owner-space",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
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
