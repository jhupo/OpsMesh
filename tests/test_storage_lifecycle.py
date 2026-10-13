"""Resource ownership at production API, Activity, tool and Worker composition boundaries.

SQL and durable execution are intentionally not exercised here. The SDK clients are
real boto3 clients with stubbed S3 responses; existing PostgreSQL/Temporal acceptance
tests cover the business behavior of these boundaries.
"""

import asyncio
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import Mock
from uuid import uuid4

import boto3
import httpx
import pytest
from botocore.stub import Stubber
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from temporalio.testing import ActivityEnvironment

from opsmesh.agents.execution.contracts import AgentExecutionContext, ToolExecutionResult
from opsmesh.agents.execution.tools.product import ProductToolDispatcher
from opsmesh.resources.files.routes import file_service
from opsmesh.resources.files.service import WorkspaceFileService
from opsmesh.resources.storage import backends as storage_module
from opsmesh.resources.storage.backends import S3ObjectStorage, create_storage
from opsmesh.resources.transfers import activities as archive_module
from opsmesh.resources.transfers.activities import ArchiveExecutionActivities
from opsmesh.resources.transfers.restore_storage import RestoreDrillStorage
from opsmesh.runtime.sandboxes import factory as sandbox_factory
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.temporal import cli as worker_cli
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session


@pytest.fixture
def s3_settings() -> Settings:
    return Settings(environment="test", storage_backend="s3", s3_bucket="opsmesh")


@pytest.fixture
def owned_clients(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[SimpleNamespace]]:
    clients: list[SimpleNamespace] = []

    def create(**_: object) -> object:
        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
        )
        original_close = client.close
        close = Mock(wraps=original_close)
        monkeypatch.setattr(client, "close", close)
        stubber = Stubber(client)
        stubber.add_response("put_object", {}, {"Bucket": "opsmesh", "Key": "probe", "Body": b"ok"})
        stubber.activate()
        clients.append(
            SimpleNamespace(client=client, close=close, cleanup=original_close, stubber=stubber)
        )
        return client

    monkeypatch.setattr(storage_module, "_create_s3_client", create)
    try:
        yield clients
    finally:
        for item in clients:
            item.stubber.deactivate()
            item.cleanup()


def _raise_outcome(outcome: str) -> None:
    if outcome == "failure":
        raise OSError("operation failed")
    if outcome == "cancelled":
        raise asyncio.CancelledError()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_factory_storage_closes_its_sdk_client_once(
    s3_settings: Settings, owned_clients: list[SimpleNamespace], outcome: str
) -> None:
    try:
        with closing(create_storage(s3_settings)) as storage:
            storage.put("probe", b"ok")
            _raise_outcome(outcome)
    except (OSError, asyncio.CancelledError):
        assert outcome != "success"
    storage.close()
    owned_clients[0].close.assert_called_once()
    owned_clients[0].stubber.assert_no_pending_responses()


def test_storage_does_not_close_a_borrowed_sdk_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    original_close = client.close
    close = Mock(wraps=original_close)
    monkeypatch.setattr(client, "close", close)
    try:
        with closing(S3ObjectStorage(bucket="opsmesh", client=client)) as storage:
            assert isinstance(storage, S3ObjectStorage)
        storage.close()
        close.assert_not_called()
        with Stubber(client) as stubber:
            stubber.add_response("head_object", {}, {"Bucket": "opsmesh", "Key": "still-open"})
            assert storage.exists("still-open")
            stubber.assert_no_pending_responses()
    finally:
        original_close()


def test_restore_drill_adapter_does_not_close_borrowed_storage_or_engine(
    s3_settings: Settings, owned_clients: list[SimpleNamespace]
) -> None:
    engine = Mock()
    with closing(create_storage(s3_settings)) as storage:
        with closing(RestoreDrillStorage(engine, storage, uuid4(), uuid4())) as receipts:
            assert isinstance(receipts, RestoreDrillStorage)
        receipts.close()
        owned_clients[0].close.assert_not_called()
        engine.dispose.assert_not_called()
        storage.put("probe", b"ok")
    owned_clients[0].close.assert_called_once()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_fastapi_file_dependency_releases_request_owned_storage(
    s3_settings: Settings, owned_clients: list[SimpleNamespace], outcome: str
) -> None:
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: s3_settings
    app.dependency_overrides[get_db_session] = lambda: Session()

    @app.get("/probe")
    async def probe(
        service: Annotated[WorkspaceFileService, Depends(file_service)],
    ) -> dict[str, bool]:
        assert isinstance(service, WorkspaceFileService)
        owned_clients[0].close.assert_not_called()
        _raise_outcome(outcome)
        return {"ok": True}

    async def verify() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            if outcome == "cancelled":
                with pytest.raises(asyncio.CancelledError):
                    await client.get("/probe")
            else:
                response = await client.get("/probe")
                assert response.status_code == (200 if outcome == "success" else 500)

    asyncio.run(verify())
    owned_clients[0].close.assert_called_once()


@pytest.mark.parametrize("borrowed", [False, True])
def test_sandbox_registry_owns_storage_and_only_its_own_docker_client(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
    borrowed: bool,
) -> None:
    docker_client = Mock()
    monkeypatch.setattr(sandbox_factory.docker, "from_env", lambda **_: docker_client)
    with build_sandbox_backends(s3_settings, docker_client if borrowed else None) as registry:
        assert registry.require("docker").client.backend_id == "docker"
        owned_clients[0].close.assert_not_called()
    registry.close()
    owned_clients[0].close.assert_called_once()
    assert docker_client.close.call_count == (0 if borrowed else 1)


@pytest.mark.parametrize("failure_stage", ["docker", "sdk", "registry"])
def test_sandbox_factory_failure_closes_all_acquired_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
    failure_stage: str,
) -> None:
    docker_client = Mock()
    monkeypatch.setattr(sandbox_factory.docker, "from_env", lambda **_: docker_client)
    fail = Mock(side_effect=OSError("construction failed"))
    if failure_stage == "docker":
        monkeypatch.setattr(sandbox_factory.docker, "from_env", fail)
    elif failure_stage == "sdk":
        monkeypatch.setattr(sandbox_factory, "DockerSandboxClient", fail)
    else:
        monkeypatch.setattr(sandbox_factory, "SandboxBackendRegistry", fail)
    with pytest.raises(OSError, match="construction failed"):
        build_sandbox_backends(s3_settings)
    owned_clients[0].close.assert_called_once()
    assert docker_client.close.call_count == (0 if failure_stage == "docker" else 1)


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_reused_product_dispatcher_closes_each_owned_storage_without_reusing_it(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
    outcome: str,
) -> None:
    context = AgentExecutionContext(workspace_id=uuid4(), run_id=uuid4(), task_id=None)
    with Session() as session:
        dispatcher = ProductToolDispatcher(session, settings=s3_settings)
        assert owned_clients == []

        def execute(**kwargs: object) -> ToolExecutionResult:
            storage = kwargs["storage"]
            assert isinstance(storage, S3ObjectStorage)
            storage.put("probe", b"ok")
            _raise_outcome(outcome)
            return ToolExecutionResult(status="completed")

        monkeypatch.setattr(dispatcher, "_execute", execute)
        for _ in range(2):
            try:
                dispatcher.execute(context=context, tool_name="list_workspace_files", arguments={})
            except (OSError, asyncio.CancelledError):
                assert outcome != "success"
        assert len(owned_clients) == 2
        for item in owned_clients:
            item.close.assert_called_once()
            item.stubber.assert_no_pending_responses()


def test_product_dispatcher_preserves_borrowed_storage(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
) -> None:
    with closing(create_storage(s3_settings)) as storage, Session() as session:
        dispatcher = ProductToolDispatcher(session, settings=s3_settings, storage=storage)
        monkeypatch.setattr(
            dispatcher, "_execute", lambda **_: ToolExecutionResult(status="completed")
        )
        dispatcher.execute(
            context=AgentExecutionContext(workspace_id=uuid4(), run_id=uuid4(), task_id=None),
            tool_name="list_workspace_files",
            arguments={},
        )
        assert len(owned_clients) == 1
        owned_clients[0].close.assert_not_called()
    owned_clients[0].close.assert_called_once()


@pytest.mark.parametrize(
    ("method", "service", "operation"),
    [
        ("_export", "WorkspaceExportService", "run_archive_export_job"),
        ("_verify", "ArchiveVerificationOperationService", "run"),
        ("_restore_drill", "WorkspaceArchiveRestoreDrillService", "run"),
    ],
)
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_archive_activity_scope_closes_storage_after_its_operation(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
    method: str,
    service: str,
    operation: str,
    outcome: str,
) -> None:
    monkeypatch.setattr(archive_module, "_authorize_request", lambda *_: "queued")

    def run(_self: object, *args: object, **kwargs: object) -> None:
        storage = kwargs.get("storage", args[-1] if args else None)
        assert isinstance(storage, S3ObjectStorage)
        storage.put("probe", b"ok")
        _raise_outcome(outcome)

    monkeypatch.setattr(getattr(archive_module, service), operation, run)
    request = ExecutionRequest(
        workspace_id=uuid4(), resource_id=uuid4(), kind=ExecutionKind.ARCHIVE_EXPORT
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        activities = ArchiveExecutionActivities(
            ActivityDependencies(Session, s3_settings), executor
        )
        try:
            getattr(activities, method)(request)
        except (OSError, asyncio.CancelledError):
            assert outcome != "success"
    owned_clients[0].close.assert_called_once()
    owned_clients[0].stubber.assert_no_pending_responses()


def test_archive_cancellation_joins_storage_io_before_closing(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
) -> None:
    admitted, finish = threading.Event(), threading.Event()
    monkeypatch.setattr(archive_module, "_authorize_request", lambda *_: "queued")

    def run(_self: object, *, request: ExecutionRequest, storage: S3ObjectStorage) -> None:
        del request
        storage.put("probe", b"ok")
        admitted.set()
        assert finish.wait(5), "The test must release bounded storage I/O"

    monkeypatch.setattr(archive_module.WorkspaceExportService, "run_archive_export_job", run)
    request = ExecutionRequest(
        workspace_id=uuid4(), resource_id=uuid4(), kind=ExecutionKind.ARCHIVE_EXPORT
    )

    async def verify() -> None:
        with ThreadPoolExecutor(max_workers=1) as executor:
            activities = ArchiveExecutionActivities(
                ActivityDependencies(Session, s3_settings), executor
            )
            task = asyncio.create_task(ActivityEnvironment().run(activities.export, request))
            try:
                assert await asyncio.to_thread(admitted.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                owned_clients[0].close.assert_not_called()
                finish.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                finish.set()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(verify())
    owned_clients[0].close.assert_called_once()


@pytest.mark.parametrize("outcome", ["shutdown", "failure", "cancelled", "construction"])
def test_worker_cli_releases_storage_after_worker_shutdown_or_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
    outcome: str,
) -> None:
    workers: list[SimpleNamespace] = []

    @asynccontextmanager
    async def database(_settings: Settings) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(engine=object(), sessions=Session)

    async def nothing(*_: object) -> None:
        return None

    def build(*_: object, **kwargs: object) -> SimpleNamespace:
        if outcome == "construction":
            raise OSError("worker construction failed")
        stopped = asyncio.Event()
        resource = kwargs.get("storage")
        if resource is not None:
            assert isinstance(resource, S3ObjectStorage)

        async def run() -> None:
            if outcome == "failure" and resource is not None:
                raise OSError("worker failed")
            if outcome == "shutdown" and resource is not None:
                return
            await stopped.wait()

        async def shutdown() -> None:
            for item in owned_clients:
                item.close.assert_not_called()
            stopped.set()

        worker = SimpleNamespace(run=run, shutdown=shutdown, stopped=stopped)
        workers.append(worker)
        return worker

    class Dispatcher:
        async def run(self, _stop: asyncio.Event) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr(worker_cli, "get_settings", lambda: s3_settings)
    monkeypatch.setattr(worker_cli, "configure_logging", lambda *_: None)
    monkeypatch.setattr(worker_cli, "register_models", lambda: None)
    monkeypatch.setattr(worker_cli, "configure_worker_telemetry", lambda *_, **__: Mock())
    monkeypatch.setattr(worker_cli, "engine", Mock())
    monkeypatch.setattr(worker_cli, "connect_temporal", lambda *_: nothing())
    monkeypatch.setattr(worker_cli, "ensure_platform_schedules", nothing)
    monkeypatch.setattr(worker_cli, "TemporalExecutionClient", Mock())
    monkeypatch.setattr(worker_cli, "temporal_database", database)
    monkeypatch.setattr(worker_cli, "SessionActivities", lambda *_: SimpleNamespace(close=nothing))
    monkeypatch.setattr(worker_cli, "build_temporal_worker", build)
    monkeypatch.setattr(worker_cli, "build_archive_temporal_worker", build)
    monkeypatch.setattr(worker_cli, "TemporalOutboxPublisher", Mock())
    monkeypatch.setattr(worker_cli, "TemporalOutboxDispatcher", lambda *_, **__: Dispatcher())
    monkeypatch.setattr(sandbox_factory.docker, "from_env", lambda **_: Mock())

    async def verify() -> None:
        task = asyncio.create_task(worker_cli.run_worker())
        if outcome == "cancelled":
            for _ in range(20):
                if len(workers) == 2:
                    break
                await asyncio.sleep(0)
            assert len(workers) == 2
            task.cancel()
        try:
            await asyncio.wait_for(task, 5)
        except (OSError, asyncio.CancelledError):
            assert outcome != "shutdown"

    asyncio.run(verify())
    assert len(owned_clients) == 2
    for item in owned_clients:
        item.close.assert_called_once()
    assert all(worker.stopped.is_set() for worker in workers)


@pytest.mark.parametrize("delete_fails", [False, True])
def test_cancelled_archive_reconciliation_joins_delete_before_route_closes_storage(
    monkeypatch: pytest.MonkeyPatch,
    s3_settings: Settings,
    owned_clients: list[SimpleNamespace],
    delete_fails: bool,
) -> None:
    """Use real PG authorization/receipts while a bounded storage delete is pending."""
    from unittest.mock import AsyncMock

    from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
    from opsmesh.resources.transfers.contracts import WorkspaceArchiveExportReconciliation
    from opsmesh.resources.transfers.models import WorkspaceExportJob
    from opsmesh.resources.transfers.routes import archive_jobs
    from opsmesh.workspaces.members.models import WorkspaceMember
    from tests.fixtures.agent_product import _seed_workspace, _session

    session = _session()
    user, workspace = _seed_workspace(session, with_default_provider=False)
    job = WorkspaceExportJob(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        export_type="workspace_archive",
        status="unknown",
        request={},
    )
    session.add(job)
    session.commit()
    membership = session.query(WorkspaceMember).filter_by(workspace_id=workspace.id).one()
    context = WorkspaceContext(AuthenticatedUser.from_model(user), workspace, membership)
    temporal = Mock()
    temporal.get_workflow_handle.return_value.describe = AsyncMock(
        return_value=SimpleNamespace(status=2)
    )
    monkeypatch.setattr(archive_jobs, "connect_temporal", AsyncMock(return_value=temporal))
    admitted, finish, completed = threading.Event(), threading.Event(), threading.Event()

    def delete(_self: object, _key: str) -> None:
        admitted.set()
        try:
            assert finish.wait(5), "The test must release its bounded storage delete"
            owned_clients[0].close.assert_not_called()
            if delete_fails:
                raise OSError("delete failed after cancellation")
        finally:
            completed.set()

    monkeypatch.setattr(S3ObjectStorage, "delete", delete)

    async def verify() -> None:
        task = asyncio.create_task(
            archive_jobs.reconcile_workspace_archive_export_job(
                job_id=job.id,
                request=WorkspaceArchiveExportReconciliation(reason="Explicit reconciliation"),
                context=context,
                session=session,
                settings=s3_settings,
            )
        )
        try:
            assert await asyncio.to_thread(admitted.wait, 5)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                owned_clients[0].close.assert_not_called()
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            finish.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(verify())
        assert completed.is_set()
        owned_clients[0].close.assert_called_once()
        session.expire_all()
        assert session.get(WorkspaceExportJob, job.id).status == "unknown"
        assert session.get(WorkspaceExportJob, job.id).error == "archive_export_cleanup_pending"
    finally:
        session.close()


def test_cancelled_snapshot_download_joins_open_and_closes_unclaimed_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cancelled native download cannot return a leaked stream to the SDK."""
    import io
    from dataclasses import replace

    from opsmesh.resources.storage.backends import LocalObjectStorage
    from opsmesh.runtime.sandboxes.snapshots import SandboxSnapshotStorage

    storage = LocalObjectStorage(str(tmp_path))
    stream = io.BytesIO(b"snapshot")
    admitted, finish = threading.Event(), threading.Event()

    def open_stream(_key: str) -> io.BytesIO:
        admitted.set()
        assert finish.wait(5), "The test must release its bounded open operation"
        return stream

    monkeypatch.setattr(storage, "open", open_stream)
    environment = ActivityEnvironment()
    environment.info = replace(environment.info, workflow_id=f"opsmesh:{uuid4()}:agent:{uuid4()}")

    async def verify() -> None:
        task = asyncio.create_task(
            environment.run(SandboxSnapshotStorage(storage).download, str(uuid4()))
        )
        try:
            assert await asyncio.to_thread(admitted.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not stream.closed
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            finish.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(verify())
    assert stream.closed
