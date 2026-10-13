"""Real PostgreSQL admission and external-write consistency under commit failures."""

from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from opsmesh.identity.users.models import User
from opsmesh.resources.storage.backends import LocalObjectStorage
from opsmesh.resources.transfers.archive_jobs import WorkspaceArchiveExportJobService
from opsmesh.resources.transfers.models import WorkspaceExportJob, WorkspaceRestoreDrillOperation
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.errors import DomainError
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.test_workspace_export_api import _headers, _product_client, _seed_workspace


@pytest.mark.parametrize("status", ["queued", "running", "unknown"])
@pytest.mark.parametrize("operation_kind", ["export", "restore"])
def test_archive_admission_sees_other_users_private_operations(tmp_path, status, operation_kind):
    client, session, _ = _product_client(tmp_path)
    _, workspace = _seed_workspace(session, email="archive-owner@example.com", slug="archives")
    users = [User(email=f"viewer-{index}@example.com", display_name="Viewer") for index in range(2)]
    session.add_all(users)
    session.flush()
    for user in users:
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="viewer"))
    foreign = Workspace(owner=users[1], name="Other", slug="other-archives", settings={})
    session.add(foreign)
    session.flush()
    session.add(WorkspaceMember(workspace_id=foreign.id, user_id=users[1].id, role="owner"))
    session.commit()
    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(users[0].id),
        json={"include_audit_events": False},
    )
    assert first.status_code == 202
    first_job = session.get(WorkspaceExportJob, UUID(first.json()["id"]))
    if operation_kind == "export":
        first_job.status = status
        private_path = f"exports/archive/jobs/{first_job.id}"
        admission_path = "exports/archive/jobs"
        expected_code = "archive_export_active"
    else:
        first_job.status = "completed"
        second_job = WorkspaceExportJob(
            workspace_id=workspace.id,
            created_by_user_id=users[1].id,
            export_type="workspace_archive",
            status="completed",
            request={},
            execution_identity={
                "user_id": str(users[1].id),
                "token_id": None,
                "token_scopes": None,
            },
            job_metadata={},
        )
        session.add(second_job)
        session.flush()
        operation = WorkspaceRestoreDrillOperation(
            workspace_id=workspace.id,
            export_job_id=first_job.id,
            user_id=users[0].id,
            temporary_workspace_id=uuid4(),
            status=status,
            storage_keys=[],
            request={},
            execution_identity=first_job.execution_identity,
            origin="user",
        )
        session.add(operation)
        session.flush()
        private_path = f"exports/archive/restore-drills/{operation.id}"
        admission_path = f"exports/archive/jobs/{second_job.id}/restore-drill"
        expected_code = "restore_drill_active"
    session.commit()
    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/{admission_path}",
        headers=_headers(users[1].id),
        json={},
    )
    assert denied.status_code == 409
    assert expected_code in denied.text
    assert (
        client.get(
            f"/api/v1/workspaces/{workspace.id}/{private_path}", headers=_headers(users[1].id)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/workspaces/{foreign.id}/exports/archive/jobs",
            headers=_headers(users[1].id),
            json={},
        ).status_code
        == 202
    )
    client.close()


@pytest.mark.parametrize("failure", ["before_commit", "lost_ack", "unconfirmed"])
def test_archive_commit_failure_preserves_only_confirmed_or_uncertain_objects(
    tmp_path, monkeypatch, failure
):
    client, session, _ = _product_client(tmp_path)
    user, workspace = _seed_workspace(session, email="commit@example.com", slug="commit-archive")
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
    commit, scalar = session.commit, session.scalar
    injected = False
    probe_pending = False

    def failing_commit():
        nonlocal injected, probe_pending
        completed = any(
            isinstance(item, WorkspaceExportJob) and item.status == "completed"
            for item in session.identity_map.values()
        )
        if completed and not injected:
            injected = True
            if failure == "lost_ack":
                commit()
            probe_pending = failure == "unconfirmed"
            raise SQLAlchemyError("Completion COMMIT acknowledgement unavailable")
        commit()

    def failing_probe(*args, **kwargs):
        nonlocal probe_pending
        if probe_pending:
            probe_pending = False
            raise SQLAlchemyError("Completion outcome query unavailable")
        return scalar(*args, **kwargs)

    monkeypatch.setattr(session, "commit", failing_commit)
    monkeypatch.setattr(session, "scalar", failing_probe)
    storage = LocalObjectStorage(str(tmp_path))
    delete = storage.delete

    def delete_without_transaction(key):
        assert not session.in_transaction()
        delete(key)

    monkeypatch.setattr(storage, "delete", delete_without_transaction)
    service = WorkspaceArchiveExportJobService(session)
    if failure == "lost_ack":
        result = service.run_archive_export_job(request=request, storage=storage)
        assert result.status == "completed"
        content = storage.read(result.storage_key)
        assert sha256(content).hexdigest() == result.checksum_sha256
        downloaded = client.get(
            f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/download",
            headers=_headers(user.id),
        )
        assert downloaded.status_code == 200 and downloaded.content == content
    else:
        with pytest.raises(DomainError if failure == "unconfirmed" else SQLAlchemyError):
            service.run_archive_export_job(request=request, storage=storage)
        session.expire_all()
        result = session.get(WorkspaceExportJob, job_id)
        assert result.status == ("unknown" if failure == "unconfirmed" else "failed")
        key = f"workspaces/{workspace.id}/exports/{job_id}/archive.zip"
        assert storage.exists(key) is (failure == "unconfirmed")
        if failure == "unconfirmed":
            with pytest.raises(DomainError, match="cannot replay"):
                service.run_archive_export_job(request=request, storage=storage)
    assert injected
    client.close()
