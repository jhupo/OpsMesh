from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest
from opsmesh_operator.installation import Installation
from opsmesh_operator.update_state import Journal, UpdatePlan
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.admin.updates import daemon
from backend.app.admin.updates.models import (
    PlatformInstallation,
    PlatformUpdateEvent,
    PlatformUpdateJob,
)
from backend.app.admin.updates.service import UpdateService, maintenance_enabled
from backend.tests.test_admin_api import _admin_headers, _client


def manifest(tag: str = "v0.1.0") -> ReleaseManifest:
    return ReleaseManifest(
        tag=tag,
        repository="jhupo/OpsMesh",
        commit="a" * 40,
        backend_digest="sha256:" + "b" * 64,
        runtime_digest="sha256:" + "c" * 64,
        database_revision="0071_platform_delivery",
        upgrade_from_revisions=["0071_platform_delivery"],
        rollback_database_revisions=["0071_platform_delivery"],
        connector_protocol=2,
        platforms=["linux/amd64"],
        files=[ReleaseFile(name="bundle.tar.gz", size=1, sha256="d" * 64)],
    )


def test_update_api_requires_platform_authority_and_durable_plan() -> None:
    client, session, _ = _client()
    body = {"tag": "v0.2.0", "idempotency_key": "test-request-1"}
    path = "/api/v1/admin/system/updates/plans"
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers=_admin_headers()).status_code == 409
    client.app.state.settings.release_update_enabled = True
    created = client.post(path, json=body, headers=_admin_headers())
    assert created.status_code == 202, created.text
    result = created.json()
    assert result["status"] == "planning"
    assert "pid" not in result and "command" not in result
    assert client.post(path, json=body, headers=_admin_headers()).json()["id"] == result["id"]
    assert (
        client.post(path, json={**body, "tag": "v0.3.0"}, headers=_admin_headers()).status_code
        == 409
    )
    assert (
        client.post(
            path, json={**body, "bundle_url": "http://evil"}, headers=_admin_headers()
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/admin/system/updates/{result['id']}/apply",
            json={"plan_sha256": "a" * 64},
            headers=_admin_headers(),
        ).status_code
        == 409
    )
    cancelled = client.post(
        f"/api/v1/admin/system/updates/{result['id']}/cancel", headers=_admin_headers()
    )
    assert cancelled.json()["status"] == "cancelled"
    assert (
        client.post("/api/v1/admin/system/update", json=body, headers=_admin_headers()).status_code
        == 404
    )


def test_plan_approval_is_bound_to_fingerprint_and_idempotent() -> None:
    _, session, _ = _client()
    service = UpdateService(session)
    job = service.request(tag="v0.2.0", action="update", key="approve-once")
    job.status, job.plan_sha256 = "ready", "e" * 64
    session.commit()
    with pytest.raises(ValueError, match="exact"):
        service.approve(job.id, "f" * 64)
    assert service.approve(job.id, "e" * 64).status == "queued"
    approved = job.approved_at
    # SQLite drops timezone metadata when the lock query refreshes this row.
    assert service.approve(job.id, "e" * 64).approved_at.replace(tzinfo=UTC) == approved
    session.commit()
    assert (
        len(
            session.scalars(
                select(PlatformUpdateEvent).where(PlatformUpdateEvent.phase == "approved")
            ).all()
        )
        == 1
    )


def test_maintenance_holds_new_plans() -> None:
    _, session, _ = _client()
    session.get(PlatformInstallation, 1).maintenance = True
    session.commit()
    assert maintenance_enabled(session)
    with pytest.raises(ValueError, match="owns"):
        UpdateService(session).request(tag="v0.2.0", action="update", key="maintenance-denial")


def test_worker_admission_does_not_claim_work_during_maintenance() -> None:
    from backend.app.workers.runner import WorkerRunner
    from backend.app.workers.runner_models import WorkerRunnerConfig
    from backend.tests.test_worker_runner import _queue, _session_factory

    factory = _session_factory()
    with factory() as session:
        session.get(PlatformInstallation, 1).maintenance = True
        session.commit()
    runner = WorkerRunner(
        queue=_queue(),
        session_factory=factory,
        config=WorkerRunnerConfig(worker_id="drain-check", queue_name="agent_runs"),
    )
    assert runner.run_once() is False


class FakeDeployment:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_health = False

    def preflight(self, target):
        self.calls.append("preflight")

    def stage(self, target):
        self.calls.append("stage")

    def stop(self):
        self.calls.append("stop")

    def migrate(self, target):
        self.calls.append("migrate")

    def switch(self, target):
        self.calls.append("switch")

    def start(self, target):
        self.calls.append("start")

    def healthy(self):
        self.calls.append("healthy")
        if self.fail_health:
            raise RuntimeError("health failure")


@pytest.mark.parametrize("health_failure", [False, True])
def test_host_workflow_is_durable_and_never_replays_interrupted_work(
    tmp_path: Path,
    monkeypatch,
    health_failure: bool,
) -> None:
    _, session, _ = _client()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(daemon, "SessionLocal", factory)
    deployment = FakeDeployment()
    deployment.fail_health = health_failure
    monkeypatch.setattr(daemon, "deployment_for", lambda _: deployment)
    monkeypatch.setattr(Installation, "current", lambda _: manifest())
    monkeypatch.setattr(daemon.ReleaseSource, "fetch_manifest", lambda *args: manifest("v0.2.0"))
    (tmp_path / ".env").write_text("TEST=example", encoding="utf-8")
    updater = daemon.HostUpdater(Installation(root=tmp_path, mode="compose"))
    monkeypatch.setattr(updater, "revision", lambda: "0071_platform_delivery")
    monkeypatch.setattr(updater.backups, "create", lambda **kwargs: str(uuid4()))
    job = UpdateService(session).request(tag="v0.2.0", action="update", key="durable-test")
    session.commit()
    updater.tick()
    session.expire_all()
    job = session.get(PlatformUpdateJob, job.id)
    assert job.status == "ready"
    UpdateService(session).approve(job.id, job.plan_sha256)
    session.commit()
    updater.tick()
    session.expire_all()
    job = session.get(PlatformUpdateJob, job.id)
    assert job.status == ("recovery_required" if health_failure else "succeeded")
    assert maintenance_enabled(session) is health_failure
    assert deployment.calls[-4:] == ["stop", "switch", "start", "healthy"]
    assert "migrate" not in deployment.calls  # Already at the target schema; do not replay it.
    before = list(deployment.calls)
    updater.tick()
    assert deployment.calls == before
    journal = Journal.model_validate_json((tmp_path / "updates" / f"{job.id}.json").read_bytes())
    assert journal.backup_id is not None
    phases = session.scalars(
        select(PlatformUpdateEvent.phase).where(PlatformUpdateEvent.job_id == job.id)
    ).all()
    assert "migrating" in phases and "health_check" in phases


def test_journal_preserves_recovery_plan(tmp_path: Path) -> None:
    plan = UpdatePlan(
        action="update",
        target=manifest("v0.2.0"),
        previous=manifest(),
        database_revision="0071_platform_delivery",
        configuration_sha256="e" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    journal = Journal(job_id=str(UUID(int=1)), plan=plan, phase="draining")
    advanced = journal.advance(tmp_path, "backup", backup_id=str(UUID(int=2)))
    restored = Journal.model_validate_json(
        (tmp_path / "updates" / f"{journal.job_id}.json").read_bytes()
    )
    assert restored == advanced
    assert restored.plan.fingerprint() == plan.fingerprint()


@pytest.mark.parametrize("strategy", ["invalid", "restore", "rollback", "resume"])
def test_recovery_rejects_unsafe_request_before_stopping_services(tmp_path, monkeypatch, strategy):
    _, session, _ = _client()
    monkeypatch.setattr(daemon, "SessionLocal", sessionmaker(bind=session.get_bind()))
    deployment = FakeDeployment()
    monkeypatch.setattr(daemon, "deployment_for", lambda _: deployment)
    updater = daemon.HostUpdater(Installation(root=tmp_path, mode="systemd"))
    plan = UpdatePlan(
        action="update",
        target=manifest("v0.2.0"),
        previous=manifest(),
        database_revision="0071_platform_delivery",
        configuration_sha256="e" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    job = UpdateService(session).request(tag="v0.2.0", action="update", key="recovery-denial")
    job.status, job.plan_sha256 = "recovery_required", plan.fingerprint()
    session.commit()
    Journal(job_id=job.id, plan=plan, phase="starting").save(tmp_path)
    monkeypatch.setattr(updater, "revision", lambda: "unknown-schema")
    with pytest.raises(ValueError):
        updater.recover(job.id, strategy)
    assert deployment.calls == []


@pytest.mark.parametrize("phase", ["rolled_back", "backup_restored", "succeeded"])
def test_crash_after_terminal_journal_reconciles_without_replaying_io(tmp_path, monkeypatch, phase):
    _, session, _ = _client()
    monkeypatch.setattr(daemon, "SessionLocal", sessionmaker(bind=session.get_bind()))
    deployment = FakeDeployment()
    monkeypatch.setattr(daemon, "deployment_for", lambda _: deployment)
    updater = daemon.HostUpdater(Installation(root=tmp_path, mode="systemd"))
    plan = UpdatePlan(
        action="update",
        target=manifest("v0.2.0"),
        previous=manifest(),
        database_revision="0071_platform_delivery",
        configuration_sha256="e" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    job = UpdateService(session).request(tag="v0.2.0", action="update", key="terminal-reconcile")
    job.status, job.plan_sha256 = "running", plan.fingerprint()
    session.get(PlatformInstallation, 1).maintenance = True
    session.commit()
    journal = Journal(job_id=job.id, plan=plan, phase="starting").advance(tmp_path, phase)
    updater.tick()
    session.expire_all()
    state = session.get(PlatformInstallation, 1)
    assert not state.maintenance
    assert state.release_manifest["tag"] == ("v0.2.0" if phase == "succeeded" else "v0.1.0")
    assert session.get(PlatformUpdateJob, job.id).active_slot is None
    assert session.get(PlatformUpdateEvent, journal.history[-1].id) is not None
    updater.tick()
    assert deployment.calls == []
