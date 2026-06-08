from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.exports.models import WorkspaceExportJob
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.notifications.models import WorkspaceNotification
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)
from backend.app.runtime_manager.dependencies import get_docker_runtime_client
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam
from backend.app.webhooks.models import WebhookDeliveryAttempt, WebhookSubscription
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"
SOURCE_MARKER = "source-secret-marker"


@dataclass(frozen=True)
class SeededIsolationData:
    user: User
    source_workspace: Workspace
    target_workspace: Workspace
    foreign_user: User
    source_file: WorkspaceFile
    source_artifact: Artifact
    source_task: Task
    source_run: AgentRun
    source_runtime_space: RuntimeSpace
    source_runtime: WorkspaceRuntime
    source_webhook: WebhookSubscription
    source_credential: ModelProviderCredential
    source_export_job: WorkspaceExportJob
    source_notification: WorkspaceNotification
    source_message_thread: AgentMessageThread


@dataclass(frozen=True)
class EndpointCase:
    name: str
    method: str
    path: Callable[[SeededIsolationData], str]
    expected_status: int
    json: dict[str, object] | None = None
    expected_total: int | None = None


ID_SCOPED_CASES = [
    EndpointCase(
        name="file download",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/files/{data.source_file.id}/download"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="artifact download",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/artifacts/{data.source_artifact.id}/download"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="artifact history",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/artifacts/history?task_id={data.source_task.id}&work_package_id=source-package"
        ),
        expected_status=200,
        expected_total=0,
    ),
    EndpointCase(
        name="task messages",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/tasks/{data.source_task.id}/messages"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="run events",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/runs/{data.source_run.id}/events"
        ),
        expected_status=200,
        expected_total=0,
    ),
    EndpointCase(
        name="run cancel",
        method="POST",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/runs/{data.source_run.id}/cancel"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="runtime space",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/runtime-spaces/{data.source_runtime_space.id}"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="runtime space events",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/runtime-spaces/{data.source_runtime_space.id}/events"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="runtime events",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/runtimes/{data.source_runtime.id}/events"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="runtime start",
        method="POST",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/runtimes/{data.source_runtime.id}/start"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="webhook disable",
        method="POST",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/webhook-subscriptions/{data.source_webhook.id}/disable"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="webhook delivery attempts",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/webhook-subscriptions/{data.source_webhook.id}/delivery-attempts"
        ),
        expected_status=200,
        expected_total=0,
    ),
    EndpointCase(
        name="model provider disable",
        method="POST",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/model-provider-credentials/{data.source_credential.id}/disable"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="export job read",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/exports/archive/jobs/{data.source_export_job.id}"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="export job download",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/exports/archive/jobs/{data.source_export_job.id}/download"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="notification read",
        method="POST",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/notifications/{data.source_notification.id}/read"
        ),
        expected_status=404,
    ),
    EndpointCase(
        name="agent message thread messages",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}"
            f"/agent-message-threads/{data.source_message_thread.id}/messages"
        ),
        expected_status=404,
    ),
]


LIST_SCOPED_CASES = [
    EndpointCase(
        name="files",
        method="GET",
        path=lambda data: f"/api/v1/workspaces/{data.target_workspace.id}/files",
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="artifacts",
        method="GET",
        path=lambda data: f"/api/v1/workspaces/{data.target_workspace.id}/artifacts",
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="tasks",
        method="GET",
        path=lambda data: f"/api/v1/workspaces/{data.target_workspace.id}/tasks",
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="runs",
        method="GET",
        path=lambda data: f"/api/v1/workspaces/{data.target_workspace.id}/runs",
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="runtime spaces",
        method="GET",
        path=lambda data: f"/api/v1/workspaces/{data.target_workspace.id}/runtime-spaces",
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="runtimes",
        method="GET",
        path=lambda data: f"/api/v1/workspaces/{data.target_workspace.id}/runtimes",
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="webhooks",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}/webhook-subscriptions"
        ),
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="model provider credentials",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}/model-provider-credentials"
        ),
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="notifications",
        method="GET",
        path=lambda data: f"/api/v1/workspaces/{data.target_workspace.id}/notifications",
        expected_status=200,
        expected_total=1,
    ),
    EndpointCase(
        name="agent message threads",
        method="GET",
        path=lambda data: (
            f"/api/v1/workspaces/{data.target_workspace.id}/agent-message-threads"
        ),
        expected_status=200,
        expected_total=1,
    ),
]


@pytest.mark.parametrize("case", ID_SCOPED_CASES, ids=lambda case: case.name)
def test_cross_workspace_id_access_is_scoped(
    tmp_path: Path,
    case: EndpointCase,
) -> None:
    client, session = _client(tmp_path)
    data = _seed_isolation_data(session, tmp_path)

    response = client.request(
        case.method,
        case.path(data),
        headers=_headers(data.user.id),
        json=case.json,
    )

    assert response.status_code == case.expected_status
    if case.expected_total is not None:
        assert response.json()["total"] == case.expected_total
        assert SOURCE_MARKER not in response.text


@pytest.mark.parametrize("case", LIST_SCOPED_CASES, ids=lambda case: case.name)
def test_cross_workspace_lists_do_not_leak_foreign_rows(
    tmp_path: Path,
    case: EndpointCase,
) -> None:
    client, session = _client(tmp_path)
    data = _seed_isolation_data(session, tmp_path)

    response = client.request(
        case.method,
        case.path(data),
        headers=_headers(data.user.id),
        json=case.json,
    )

    assert response.status_code == case.expected_status
    assert response.json()["total"] == case.expected_total
    assert SOURCE_MARKER not in response.text


def test_cross_workspace_bulk_notification_update_is_empty_result(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    data = _seed_isolation_data(session, tmp_path)

    response = client.post(
        f"/api/v1/workspaces/{data.target_workspace.id}/notifications/mark-read",
        headers=_headers(data.user.id),
        json={"notification_ids": [str(data.source_notification.id)]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": str(data.target_workspace.id),
        "updated_count": 0,
    }
    session.refresh(data.source_notification)
    assert data.source_notification.read_at is None


def test_non_member_workspace_access_is_forbidden(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    data = _seed_isolation_data(session, tmp_path)

    response = client.get(
        f"/api/v1/workspaces/{data.source_workspace.id}/files",
        headers=_headers(data.foreign_user.id),
    )

    assert response.status_code == 403


def _client(tmp_path: Path) -> tuple[TestClient, Session]:
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
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            database_url="sqlite+pysqlite:///:memory:",
            storage_root=str(tmp_path),
            credential_encryption_secret="test-credential-secret",
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
    app.dependency_overrides[get_docker_runtime_client] = lambda: FakeDockerClient()
    return TestClient(app), seed_session


def _seed_isolation_data(session: Session, tmp_path: Path) -> SeededIsolationData:
    now = datetime(2026, 6, 6, 1, 0, tzinfo=UTC)
    user = User(email="shared-owner@example.com", display_name="Shared Owner")
    foreign_user = User(email="foreign-user@example.com", display_name="Foreign User")
    source_workspace = Workspace(
        owner=user,
        name="Source Workspace",
        slug="source-workspace",
        settings={},
    )
    target_workspace = Workspace(
        owner=user,
        name="Target Workspace",
        slug="target-workspace",
        settings={},
    )
    session.add_all(
        [
            user,
            foreign_user,
            source_workspace,
            target_workspace,
            WorkspaceMember(workspace=source_workspace, user=user, role="owner"),
            WorkspaceMember(workspace=target_workspace, user=user, role="owner"),
        ]
    )
    session.flush()

    source_file = _workspace_file(
        workspace_id=source_workspace.id,
        user_id=user.id,
        filename=f"{SOURCE_MARKER}-file.txt",
        content=b"source file",
    )
    target_file = _workspace_file(
        workspace_id=target_workspace.id,
        user_id=user.id,
        filename="target-file.txt",
        content=b"target file",
    )
    source_team = AgentTeam(
        workspace_id=source_workspace.id,
        name=f"{SOURCE_MARKER} team",
        team_type="software",
    )
    target_team = AgentTeam(
        workspace_id=target_workspace.id,
        name="target team",
        team_type="software",
    )
    session.add_all([source_team, target_team])
    session.flush()
    source_task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=user.id,
        agent_team_id=source_team.id,
        title=f"{SOURCE_MARKER} task",
        status="queued",
    )
    target_task = Task(
        workspace_id=target_workspace.id,
        created_by_user_id=user.id,
        agent_team_id=target_team.id,
        title="target task",
        status="queued",
    )
    source_runtime_space = RuntimeSpace(
        workspace_id=source_workspace.id,
        created_by_user_id=user.id,
        name=f"{SOURCE_MARKER} runtime space",
        scope="workspace",
    )
    target_runtime_space = RuntimeSpace(
        workspace_id=target_workspace.id,
        created_by_user_id=user.id,
        name="target runtime space",
        scope="workspace",
    )
    source_sender = AgentProfile(
        workspace_id=source_workspace.id,
        name=f"{SOURCE_MARKER} sender",
        role="planner",
    )
    source_recipient = AgentProfile(
        workspace_id=source_workspace.id,
        name=f"{SOURCE_MARKER} recipient",
        role="builder",
    )
    target_sender = AgentProfile(
        workspace_id=target_workspace.id,
        name="target sender",
        role="planner",
    )
    target_recipient = AgentProfile(
        workspace_id=target_workspace.id,
        name="target recipient",
        role="builder",
    )
    session.add_all(
        [
            source_file,
            target_file,
            source_task,
            target_task,
            source_runtime_space,
            target_runtime_space,
            source_sender,
            source_recipient,
            target_sender,
            target_recipient,
        ]
    )
    session.flush()

    source_step = TaskStep(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        title=f"{SOURCE_MARKER} step",
        work_package_id="source-package",
    )
    source_run = AgentRun(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        runtime_space_id=source_runtime_space.id,
        status="running",
        input={"marker": SOURCE_MARKER},
    )
    target_run = AgentRun(
        workspace_id=target_workspace.id,
        task_id=target_task.id,
        runtime_space_id=target_runtime_space.id,
        status="running",
    )
    source_runtime = WorkspaceRuntime(
        workspace_id=source_workspace.id,
        runtime_space_id=source_runtime_space.id,
        name=f"{SOURCE_MARKER} runtime",
        docker_container_id="source-container",
        status="running",
    )
    target_runtime = WorkspaceRuntime(
        workspace_id=target_workspace.id,
        runtime_space_id=target_runtime_space.id,
        name="target runtime",
        docker_container_id="target-container",
        status="running",
    )
    source_webhook = WebhookSubscription(
        workspace_id=source_workspace.id,
        created_by_user_id=user.id,
        name=f"{SOURCE_MARKER} webhook",
        target_url="https://hooks.example.test/source",
        event_types=["*"],
        encrypted_signing_secret="encrypted-source",
        signing_secret_fingerprint="sha256:source",
        encryption_key_id="test",
    )
    target_webhook = WebhookSubscription(
        workspace_id=target_workspace.id,
        created_by_user_id=user.id,
        name="target webhook",
        target_url="https://hooks.example.test/target",
        event_types=["*"],
        encrypted_signing_secret="encrypted-target",
        signing_secret_fingerprint="sha256:target",
        encryption_key_id="test",
    )
    source_credential = _model_provider_credential(
        workspace_id=source_workspace.id,
        user_id=user.id,
        name=f"{SOURCE_MARKER} credential",
    )
    target_credential = _model_provider_credential(
        workspace_id=target_workspace.id,
        user_id=user.id,
        name="target credential",
    )
    source_thread = AgentMessageThread(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        agent_team_id=source_team.id,
        subject=f"{SOURCE_MARKER} thread",
    )
    target_thread = AgentMessageThread(
        workspace_id=target_workspace.id,
        task_id=target_task.id,
        agent_team_id=target_team.id,
        subject="target thread",
    )
    session.add_all(
        [
            source_step,
            source_run,
            target_run,
            source_runtime,
            target_runtime,
            source_webhook,
            target_webhook,
            source_credential,
            target_credential,
            source_thread,
            target_thread,
        ]
    )
    session.flush()

    source_artifact = _artifact(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        run_id=source_run.id,
        step_id=source_step.id,
        filename=f"{SOURCE_MARKER}-artifact.txt",
        content=b"source artifact",
        created_at=now,
    )
    target_artifact = _artifact(
        workspace_id=target_workspace.id,
        task_id=target_task.id,
        run_id=target_run.id,
        step_id=None,
        filename="target-artifact.txt",
        content=b"target artifact",
        created_at=now,
    )
    source_export_job = _export_job(
        workspace_id=source_workspace.id,
        user_id=user.id,
        filename=f"{SOURCE_MARKER}-archive.zip",
        content=b"source archive",
        completed_at=now,
    )
    target_export_job = _export_job(
        workspace_id=target_workspace.id,
        user_id=user.id,
        filename="target-archive.zip",
        content=b"target archive",
        completed_at=now,
    )
    source_notification = WorkspaceNotification(
        workspace_id=source_workspace.id,
        title=f"{SOURCE_MARKER} notification",
        body="",
        notification_type="runtime",
        severity="warning",
        source_type="runtime",
        metadata_={"marker": SOURCE_MARKER},
        created_at=now,
        updated_at=now,
    )
    target_notification = WorkspaceNotification(
        workspace_id=target_workspace.id,
        title="target notification",
        body="",
        notification_type="runtime",
        severity="warning",
        source_type="runtime",
        created_at=now,
        updated_at=now,
    )
    session.add_all(
        [
            source_artifact,
            target_artifact,
            source_export_job,
            target_export_job,
            source_notification,
            target_notification,
            TaskMessage(
                workspace_id=source_workspace.id,
                task_id=source_task.id,
                task_step_id=source_step.id,
                agent_run_id=source_run.id,
                message_type="status",
                sequence=1,
                body=SOURCE_MARKER,
            ),
            RunEvent(
                workspace_id=source_workspace.id,
                agent_run_id=source_run.id,
                event_type="run.started",
                sequence=1,
                message=SOURCE_MARKER,
                event_metadata={"marker": SOURCE_MARKER},
                created_at=now,
            ),
            RuntimeSpaceEvent(
                workspace_id=source_workspace.id,
                runtime_space_id=source_runtime_space.id,
                event_type="runtime_space.created",
                message=SOURCE_MARKER,
                event_metadata={"marker": SOURCE_MARKER},
                created_at=now,
            ),
            RuntimeEvent(
                workspace_id=source_workspace.id,
                workspace_runtime_id=source_runtime.id,
                runtime_space_id=source_runtime_space.id,
                event_type="runtime.started",
                message=SOURCE_MARKER,
                event_metadata={"marker": SOURCE_MARKER},
                created_at=now,
            ),
            WebhookDeliveryAttempt(
                workspace_id=source_workspace.id,
                subscription_id=source_webhook.id,
                event_id="evt-source",
                event_type="task.created",
                payload={"marker": SOURCE_MARKER},
                available_at=now,
            ),
            AgentMessage(
                workspace_id=source_workspace.id,
                thread_id=source_thread.id,
                task_id=source_task.id,
                agent_team_id=source_team.id,
                sender_agent_profile_id=source_sender.id,
                recipient_agent_profile_id=source_recipient.id,
                body=SOURCE_MARKER,
            ),
            AgentMessage(
                workspace_id=target_workspace.id,
                thread_id=target_thread.id,
                task_id=target_task.id,
                agent_team_id=target_team.id,
                sender_agent_profile_id=target_sender.id,
                recipient_agent_profile_id=target_recipient.id,
                body="target message",
            ),
        ]
    )
    LocalStorage(str(tmp_path)).write(source_file.storage_key, b"source file")
    LocalStorage(str(tmp_path)).write(target_file.storage_key, b"target file")
    LocalStorage(str(tmp_path)).write(source_artifact.storage_key, b"source artifact")
    LocalStorage(str(tmp_path)).write(target_artifact.storage_key, b"target artifact")
    LocalStorage(str(tmp_path)).write(source_export_job.storage_key or "", b"source archive")
    LocalStorage(str(tmp_path)).write(target_export_job.storage_key or "", b"target archive")
    session.commit()
    return SeededIsolationData(
        user=user,
        source_workspace=source_workspace,
        target_workspace=target_workspace,
        foreign_user=foreign_user,
        source_file=source_file,
        source_artifact=source_artifact,
        source_task=source_task,
        source_run=source_run,
        source_runtime_space=source_runtime_space,
        source_runtime=source_runtime,
        source_webhook=source_webhook,
        source_credential=source_credential,
        source_export_job=source_export_job,
        source_notification=source_notification,
        source_message_thread=source_thread,
    )


def _workspace_file(
    *,
    workspace_id: UUID,
    user_id: UUID,
    filename: str,
    content: bytes,
) -> WorkspaceFile:
    checksum = sha256(content).hexdigest()
    return WorkspaceFile(
        workspace_id=workspace_id,
        uploaded_by_user_id=user_id,
        filename=filename,
        content_type="text/plain",
        size_bytes=len(content),
        checksum_sha256=checksum,
        storage_key=f"workspaces/{workspace_id}/files/{checksum}/{filename}",
    )


def _artifact(
    *,
    workspace_id: UUID,
    task_id: UUID,
    run_id: UUID,
    step_id: UUID | None,
    filename: str,
    content: bytes,
    created_at: datetime,
) -> Artifact:
    checksum = sha256(content).hexdigest()
    return Artifact(
        workspace_id=workspace_id,
        task_id=task_id,
        agent_run_id=run_id,
        task_step_id=step_id,
        work_package_id="source-package",
        artifact_type="report",
        filename=filename,
        content_type="text/plain",
        size_bytes=len(content),
        checksum_sha256=checksum,
        storage_key=f"workspaces/{workspace_id}/artifacts/{checksum}/{filename}",
        created_at=created_at,
    )


def _export_job(
    *,
    workspace_id: UUID,
    user_id: UUID,
    filename: str,
    content: bytes,
    completed_at: datetime,
) -> WorkspaceExportJob:
    checksum = sha256(content).hexdigest()
    return WorkspaceExportJob(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        export_type="archive",
        status="completed",
        request={"include_file_bytes": True},
        storage_key=f"workspaces/{workspace_id}/exports/{filename}",
        filename=filename,
        content_type="application/zip",
        size_bytes=len(content),
        checksum_sha256=checksum,
        completed_at=completed_at,
    )


def _model_provider_credential(
    *,
    workspace_id: UUID,
    user_id: UUID,
    name: str,
) -> ModelProviderCredential:
    return ModelProviderCredential(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name=name,
        provider="openai",
        default_model="gpt-4.1",
        encrypted_api_key=f"encrypted-{name}",
        api_key_fingerprint=f"sha256:{name}",
        encryption_key_id="test",
        is_default=False,
        status="active",
    )


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


class FakeDockerClient(DockerRuntimeClient):
    def create_container(self, request: RuntimeCreateRequest) -> str:
        return "container"

    def start_container(self, container_id: str) -> None:
        return None

    def stop_container(self, container_id: str) -> None:
        return None

    def remove_container(self, container_id: str) -> None:
        return None

    def remove_volume(self, volume_name: str) -> None:
        return None

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult:
        return RuntimeCommandResult(exit_code=0, stdout="", stderr="")
