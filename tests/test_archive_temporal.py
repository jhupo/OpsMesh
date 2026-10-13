"""Archive admission, native durable execution and non-replayed uncertain writes."""

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import UUID
from zipfile import ZipFile

import pytest
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.identity.auth.service import AuthenticationService
from opsmesh.identity.authorization.errors import AuthenticationError
from opsmesh.identity.authorization.models import ResourceGrant
from opsmesh.identity.users.models import User
from opsmesh.resources.storage.backends import LocalObjectStorage
from opsmesh.resources.transfers import activities as archive_activities
from opsmesh.resources.transfers.activities import (
    ArchiveControlActivities,
    ArchiveExecutionActivities,
)
from opsmesh.resources.transfers.archive_jobs import WorkspaceArchiveExportJobService
from opsmesh.resources.transfers.archive_ownership import archive_export_ownership
from opsmesh.resources.transfers.models import WorkspaceExportJob
from opsmesh.resources.transfers.restore_operations import restore_drill_engine
from opsmesh.resources.transfers.routes import archive_jobs as routes
from opsmesh.resources.transfers.verification_operations import ArchiveVerificationOperationService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import DispatchRequest, ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.workflows import (
    ArchiveExportWorkflow,
    ArchiveRestoreDrillWorkflow,
    ArchiveVerificationWorkflow,
)
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import DomainError
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.archive_workflows import ArchiveControlProbe
from tests.test_workspace_export_api import _headers, _product_client, _seed_workspace


def test_archive_binary_payload_respects_the_same_file_grants_as_metadata(tmp_path):
    client, session, _ = _product_client(tmp_path)
    owner, workspace = _seed_workspace(
        session, email="archive-grants@example.com", slug="archive-grants"
    )
    files = []
    for filename, content in (("allowed.txt", b"allowed"), ("private.txt", b"private-canary")):
        uploaded = client.post(
            f"/api/v1/workspaces/{workspace.id}/files",
            headers=_headers(owner.id),
            files={"file": (filename, content, "text/plain")},
        )
        assert uploaded.status_code == 201
        files.append(UUID(uploaded.json()["id"]))
    viewer = User(email="archive-viewer@example.com", display_name="Viewer")
    session.add(viewer)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=viewer.id, role="viewer"))
    session.flush()
    session.add(
        ResourceGrant(
            workspace_id=workspace.id,
            resource_kind="file",
            resource_id=files[0],
            user_id=viewer.id,
            action="read",
        )
    )
    session.commit()
    admitted = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(viewer.id),
        json={"include_audit_events": False},
    )
    assert admitted.status_code == 202
    job_id = UUID(admitted.json()["id"])
    reference = ExecutionRequest(
        workspace_id=workspace.id,
        execution_id=job_id,
        resource_id=job_id,
        kind=ExecutionKind.ARCHIVE_EXPORT,
        requested_by_user_id=viewer.id,
    )
    WorkspaceArchiveExportJobService(session).run_archive_export_job(
        request=reference, storage=LocalObjectStorage(str(tmp_path))
    )
    downloaded = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/download",
        headers=_headers(viewer.id),
    )
    assert downloaded.status_code == 200
    with ZipFile(BytesIO(downloaded.content)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert [item["id"] for item in metadata["files"]] == [str(files[0])]
        assert archive.read(f"files/{files[0]}/allowed.txt") == b"allowed"
        assert f"files/{files[1]}/private.txt" not in archive.namelist()
        assert b"private-canary" not in b"".join(archive.read(name) for name in archive.namelist())
    admin_other_identity = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/download",
        headers=_headers(owner.id),
    )
    assert admin_other_identity.status_code == 403
    client.close()


def test_archive_source_refuses_changed_or_revoked_token_and_preserves_private_ownership(tmp_path):
    client, session, _ = _product_client(tmp_path)
    owner, workspace = _seed_workspace(
        session, email="archive-token@example.com", slug="archive-token"
    )
    admitted = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )
    assert admitted.status_code == 202
    job_id = UUID(admitted.json()["id"])
    reference = ExecutionRequest(
        workspace_id=workspace.id,
        execution_id=job_id,
        resource_id=job_id,
        kind=ExecutionKind.ARCHIVE_EXPORT,
        requested_by_user_id=owner.id,
    )
    WorkspaceArchiveExportJobService(session).run_archive_export_job(
        request=reference, storage=LocalObjectStorage(str(tmp_path))
    )
    authentication = AuthenticationService(session)
    restricted = authentication.create_user_api_token(
        user_id=owner.id,
        name="Read only archive",
        settings=client.app.state.settings,
        scopes={"workspace_ids": [str(workspace.id)], "workspace_actions": ["read"]},
    )
    headers = {"Authorization": "Bearer " + restricted.token}
    path = f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}"
    assert client.get(path + "/download", headers=headers).status_code == 403
    assert client.post(path + "/verify", headers=headers).status_code == 403
    assert client.post(path + "/restore-drill", headers=headers, json={}).status_code == 403
    own = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=headers,
        json={"include_audit_events": False},
    )
    assert own.status_code == 202
    own_id = UUID(own.json()["id"])
    own_reference = reference.model_copy(update={"execution_id": own_id, "resource_id": own_id})
    WorkspaceArchiveExportJobService(session).run_archive_export_job(
        request=own_reference, storage=LocalObjectStorage(str(tmp_path))
    )
    own_path = f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{own_id}"
    assert client.get(own_path + "/download", headers=headers).status_code == 200
    assert client.get(own_path + "/download", headers=_headers(owner.id)).status_code == 403
    viewer = User(email="archive-stranger@example.com", display_name="Stranger")
    session.add(viewer)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=viewer.id, role="viewer"))
    session.commit()
    assert client.get(own_path, headers=_headers(viewer.id)).status_code == 404
    verification = client.post(own_path + "/verify", headers=headers)
    assert verification.status_code == 202
    authentication.revoke_user_api_token(user_id=owner.id, token_id=restricted.record.id)
    verify_id = UUID(verification.json()["id"])
    verify_reference = reference.model_copy(
        update={
            "kind": ExecutionKind.ARCHIVE_VERIFY,
            "execution_id": verify_id,
            "resource_id": verify_id,
        }
    )
    with pytest.raises(AuthenticationError):
        ArchiveVerificationOperationService(session).run(
            verify_reference, LocalObjectStorage(str(tmp_path))
        )
    assert client.get(own_path + "/download", headers=headers).status_code == 401
    client.close()


@pytest.mark.parametrize("revoke_after_write", [False, True])
def test_archive_io_ends_transactions_and_rechecks_current_authority(tmp_path, revoke_after_write):
    client, session, sessions = _product_client(tmp_path)
    user, workspace = _seed_workspace(session, email="archive-io@example.com", slug="archive-io")
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(user.id),
        files={"file": ("input.txt", b"authorized input", "text/plain")},
    )
    assert uploaded.status_code == 201
    admitted = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(user.id),
        json={"include_audit_events": False},
    )
    assert admitted.status_code == 202
    job_id = UUID(admitted.json()["id"])
    request = ExecutionRequest(
        workspace_id=workspace.id,
        execution_id=job_id,
        resource_id=job_id,
        kind=ExecutionKind.ARCHIVE_EXPORT,
        requested_by_user_id=user.id,
    )
    reads, writes, removals = [], [], []

    class ObservedStorage(LocalObjectStorage):
        def read(self, key):
            assert not session.in_transaction()
            reads.append(key)
            return super().read(key)

        def exists(self, key):
            assert not session.in_transaction()
            return super().exists(key)

        def write(self, key, content):
            assert not session.in_transaction()
            writes.append(key)
            super().write(key, content)
            if revoke_after_write:
                with sessions() as authority:
                    authority.get(User, user.id).status = "disabled"
                    authority.commit()

        def delete(self, key):
            assert not session.in_transaction()
            removals.append(key)
            super().delete(key)

    storage = ObservedStorage(str(tmp_path))
    service = WorkspaceArchiveExportJobService(session)
    if revoke_after_write:
        with pytest.raises(AuthenticationError):
            service.run_archive_export_job(request=request, storage=storage)
    else:
        service.run_archive_export_job(request=request, storage=storage)
    session.expire_all()
    job = session.get(WorkspaceExportJob, request.resource_id)
    assert job.status == ("failed" if revoke_after_write else "completed")
    assert len(reads) == len(writes) == 1
    assert len(removals) == int(revoke_after_write)
    assert LocalObjectStorage(str(tmp_path)).exists(writes[0]) is not revoke_after_write
    client.close()


def test_native_archive_export_verify_and_restore_flow(tmp_path):
    client, session, sessions = _product_client(tmp_path)
    user, workspace = _seed_workspace(
        session, email="native-archive@example.com", slug="native-archive"
    )
    _, foreign = _seed_workspace(session, email="foreign-native@example.com", slug="foreign-native")
    control = ArchiveControlActivities(
        ActivityDependencies(
            sessions,
            Settings(
                environment="test", storage_root=str(tmp_path), temporal_task_queue="archive-tests"
            ),
        )
    )

    async def dispatch(native):
        session.expire_all()
        row = session.scalars(
            select(TemporalOutboxEntry).order_by(TemporalOutboxEntry.created_at.desc())
        ).first()
        payload = DispatchRequest.model_validate(row.payload)
        await TemporalExecutionClient(native, task_queue="archive-tests").dispatch(payload)
        await native.get_workflow_handle(payload.workflow_id).result()

    async def scenario():
        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        with ThreadPoolExecutor(max_workers=4) as executor:
            activities = ArchiveExecutionActivities(
                ActivityDependencies(
                    sessions, Settings(environment="test", storage_root=str(tmp_path))
                ),
                executor,
            )
            try:
                async with (
                    Worker(
                        environment.client,
                        task_queue="archive-tests",
                        workflows=[
                            ArchiveExportWorkflow,
                            ArchiveVerificationWorkflow,
                            ArchiveRestoreDrillWorkflow,
                        ],
                        activities=[
                            control.prepare,
                            control.interrupted,
                            control.verify_failed,
                            control.restore_drill_interrupted,
                        ],
                        activity_executor=executor,
                    ),
                    Worker(
                        environment.client,
                        task_queue="archive-tests-archives",
                        activities=[activities.export, activities.verify, activities.restore_drill],
                        max_concurrent_activities=4,
                    ),
                ):
                    created = client.post(
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
                        headers=_headers(user.id),
                        json={"include_audit_events": False},
                    )
                    assert created.status_code == 202 and created.json()["status"] == "queued"
                    job_id = created.json()["id"]
                    await dispatch(environment.client)
                    verify = client.post(
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/verify",
                        headers=_headers(user.id),
                    )
                    assert verify.status_code == 202 and verify.json()["result"] is None
                    duplicate = client.post(
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/verify",
                        headers=_headers(user.id),
                    )
                    assert duplicate.json()["id"] == verify.json()["id"]
                    await dispatch(environment.client)
                    checked = client.get(
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/verifications/{verify.json()['id']}",
                        headers=_headers(user.id),
                    )
                    assert (
                        checked.status_code == 200 and checked.json()["result"]["verified"] is True
                    )
                    denied = client.get(
                        f"/api/v1/workspaces/{foreign.id}/exports/archive/verifications/{verify.json()['id']}",
                        headers=_headers(foreign.owner_user_id),
                    )
                    assert denied.status_code == 404
                    restore = client.post(
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/restore-drill",
                        headers=_headers(user.id),
                        json={},
                    )
                    assert restore.status_code == 202 and restore.json()["result"] is None
                    await dispatch(environment.client)
                    drilled = client.get(
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/restore-drills/{restore.json()['id']}",
                        headers=_headers(user.id),
                    )
                    assert drilled.status_code == 200 and drilled.json()["status"] == "completed"
                    assert drilled.json()["result"]["passed"] is True
                    assert "storage_keys" not in drilled.json()
            finally:
                await environment.shutdown()

    asyncio.run(scenario())
    client.close()


def test_archive_queue_saturation_preserves_control_and_joins_cancelled_writes(
    tmp_path, monkeypatch
):
    client, session, sessions = _product_client(tmp_path)
    users = [
        _seed_workspace(session, email=f"capacity-{i}@example.com", slug=f"capacity-{i}")
        for i in range(2)
    ]
    dispatches = []
    for user, workspace in users:
        reply = client.post(
            f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
            headers=_headers(user.id),
            json={"include_audit_events": False},
        )
        assert reply.status_code == 202
        row = session.scalars(
            select(TemporalOutboxEntry).where(TemporalOutboxEntry.workspace_id == workspace.id)
        ).one()
        dispatches.append(DispatchRequest.model_validate(row.payload))
    first = dispatches[0].execution
    assert first is not None
    started, release, cancellation_observed = Event(), Event(), Event()
    writes = []

    class SlowStorage(LocalObjectStorage):
        def write(self, key, content):
            writes.append(key)
            if str(first.resource_id) in key:
                started.set()
                deadline = monotonic() + 30
                while not release.wait(timeout=0.05):
                    if activity.is_cancelled():
                        cancellation_observed.set()
                    assert monotonic() < deadline, "test did not release the bounded writer"
            super().write(key, content)

    monkeypatch.setattr(
        archive_activities, "create_storage", lambda settings: SlowStorage(str(tmp_path))
    )
    settings = Settings(
        environment="test", storage_root=str(tmp_path), temporal_task_queue="archive-capacity"
    )
    control = ArchiveControlActivities(ActivityDependencies(sessions, settings))

    async def scenario():
        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        with (
            ThreadPoolExecutor(max_workers=2) as control_executor,
            ThreadPoolExecutor(max_workers=1) as io_executor,
        ):
            execute = ArchiveExecutionActivities(
                ActivityDependencies(sessions, settings), io_executor
            )
            try:
                async with (
                    Worker(
                        environment.client,
                        task_queue=settings.temporal_task_queue,
                        workflows=[ArchiveExportWorkflow, ArchiveControlProbe],
                        activities=[control.prepare, control.interrupted],
                        activity_executor=control_executor,
                    ),
                    Worker(
                        environment.client,
                        task_queue=settings.temporal_task_queue + "-archives",
                        activities=[execute.export],
                        max_concurrent_activities=1,
                        max_heartbeat_throttle_interval=timedelta(seconds=1),
                    ),
                ):
                    dispatcher = TemporalExecutionClient(
                        environment.client, task_queue=settings.temporal_task_queue
                    )
                    await dispatcher.dispatch(dispatches[0])
                    assert await asyncio.to_thread(started.wait, 20)
                    await dispatcher.dispatch(dispatches[1])
                    second = dispatches[1].execution
                    assert second is not None
                    probe = await environment.client.start_workflow(
                        ArchiveControlProbe.run,
                        second,
                        id="archive-capacity-probe",
                        task_queue=settings.temporal_task_queue,
                    )
                    assert await probe.result() == settings.temporal_task_queue + "-archives"
                    session.expire_all()
                    assert session.get(WorkspaceExportJob, second.resource_id).status == "queued"
                    handle = environment.client.get_workflow_handle(first.workflow_id)
                    await handle.cancel()
                    with (
                        pytest.raises(DomainError, match="still executing"),
                        archive_export_ownership(session, first.resource_id),
                    ):
                        pass
                    assert await asyncio.to_thread(cancellation_observed.wait, 15)
                    release.set()
                    with pytest.raises(WorkflowFailureError):
                        await handle.result()
                    await environment.client.get_workflow_handle(second.workflow_id).result()
                    session.expire_all()
                    assert session.get(WorkspaceExportJob, first.resource_id).status == "completed"
                    assert session.get(WorkspaceExportJob, second.resource_id).status == "completed"
                    assert len(writes) == 2 and len(set(writes)) == 2
            finally:
                release.set()
                await environment.shutdown()

    asyncio.run(scenario())
    client.close()


def test_native_archive_writer_death_preserves_unknown_output_until_explicit_reconcile(
    tmp_path, monkeypatch
):
    client, session, sessions = _product_client(tmp_path)
    user, workspace = _seed_workspace(
        session, email="archive-death@example.com", slug="archive-death"
    )
    foreign_user, foreign = _seed_workspace(
        session, email="foreign-death@example.com", slug="foreign-death"
    )
    admitted = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(user.id),
        json={"include_audit_events": False},
    )
    assert admitted.status_code == 202
    job_id = UUID(admitted.json()["id"])
    row = session.scalars(select(TemporalOutboxEntry)).one()
    dispatch = DispatchRequest.model_validate(row.payload)
    request = ExecutionRequest.model_validate(row.payload["execution"])
    engine = restore_drill_engine(session)
    activities = ArchiveControlActivities(
        ActivityDependencies(
            sessions,
            Settings(
                environment="test", storage_root=str(tmp_path), temporal_task_queue="archive-death"
            ),
        )
    )
    attempts = []

    @activity.defn(name="opsmesh.archive.export")
    async def dying_writer(item: ExecutionRequest):
        attempts.append(activity.info().attempt)
        crash = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                str(Path("tests/fixtures/archive_crash.py").resolve()),
                engine.url.render_as_string(hide_password=False),
                str(item.workspace_id),
                str(user.id),
                str(item.resource_id),
                str(tmp_path),
            ],
            capture_output=True,
            timeout=60,
        )
        assert crash.returncode == 23, crash.stderr.decode(errors="replace")
        raise ApplicationError("Archive process died after external write", type="ProcessLost")

    async def scenario():
        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)

        async def connect(settings):
            return environment.client

        monkeypatch.setattr(routes, "connect_temporal", connect)
        with ThreadPoolExecutor(max_workers=3) as executor:
            try:
                async with (
                    Worker(
                        environment.client,
                        task_queue="archive-death",
                        workflows=[ArchiveExportWorkflow],
                        activities=[activities.prepare, activities.interrupted],
                        activity_executor=executor,
                    ),
                    Worker(
                        environment.client,
                        task_queue="archive-death-archives",
                        activities=[dying_writer],
                        max_concurrent_activities=1,
                    ),
                ):
                    await TemporalExecutionClient(
                        environment.client, task_queue="archive-death"
                    ).dispatch(dispatch)
                    with pytest.raises(WorkflowFailureError):
                        await environment.client.get_workflow_handle(request.workflow_id).result()
                    session.expire_all()
                    assert session.get(WorkspaceExportJob, job_id).status == "unknown"
                    storage = LocalObjectStorage(str(tmp_path))
                    key = f"workspaces/{workspace.id}/exports/{job_id}/archive.zip"
                    assert storage.exists(key)
                    blocked = client.post(
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
                        headers=_headers(user.id),
                        json={},
                    )
                    assert blocked.status_code == 409
                    with pytest.raises(DomainError, match="cannot replay"):
                        WorkspaceArchiveExportJobService(session).run_archive_export_job(
                            request=request, storage=storage
                        )
                    session.rollback()
                    foreign_reply = await asyncio.to_thread(
                        client.post,
                        f"/api/v1/workspaces/{foreign.id}/exports/archive/jobs/{job_id}/reconcile",
                        headers=_headers(foreign_user.id),
                        json={"reason": "foreign object"},
                    )
                    assert foreign_reply.status_code == 404 and storage.exists(key)
                    with archive_export_ownership(session, job_id):
                        still_owned = await asyncio.to_thread(
                            client.post,
                            f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/reconcile",
                            headers=_headers(user.id),
                            json={"reason": "cannot race live writer"},
                        )
                        assert still_owned.status_code == 409 and storage.exists(key)
                    reconciled = await asyncio.to_thread(
                        client.post,
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/reconcile",
                        headers=_headers(user.id),
                        json={"reason": "Writer died; discard uncertain file"},
                    )
                    assert (
                        reconciled.status_code == 200
                        and reconciled.json()["status"] == "reconciled"
                    )
                    assert not storage.exists(key)
                    repeated = await asyncio.to_thread(
                        client.post,
                        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/reconcile",
                        headers=_headers(user.id),
                        json={"reason": "Same confirmation"},
                    )
                    assert repeated.status_code == 200
                    assert (
                        client.post(
                            f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
                            headers=_headers(user.id),
                            json={},
                        ).status_code
                        == 202
                    )
            finally:
                await environment.shutdown()

    asyncio.run(scenario())
    assert attempts == [1]
    client.close()
