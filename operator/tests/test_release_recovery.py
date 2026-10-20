"""Host failures must retain a usable journal and stop before unsafe effects."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("fcntl", reason="Host maintenance requires Linux file locks")

from opsmesh_operator import releases_apply as releases
from opsmesh_operator.backups import BackupStore
from opsmesh_operator.contracts import ReleaseManifest
from opsmesh_operator.installation import Installation
from opsmesh_operator.temporal_backup import TemporalBackup


@pytest.fixture
def host(tmp_path, monkeypatch):
    manifest = ReleaseManifest(
        tag="v0.1.0rc23",
        commit="a" * 40,
        repository="jhupo/OpsMesh",
        api_image="sha256:" + "b" * 64,
        worker_image="sha256:" + "c" * 64,
        runtime_image="sha256:" + "d" * 64,
        database_revision="0001_v4_initial",
        upgrade_from_revisions=[],
        rollback_database_revisions=[],
        platforms=["linux/amd64"],
        files=[{"name": "server.tar.gz", "sha256": "e" * 64, "size": 1}],
    )
    current = tmp_path / "current"
    current.mkdir()
    (current / "release-manifest.json").write_text(manifest.model_dump_json())
    (tmp_path / "operations").mkdir()
    installation = Installation(root=tmp_path, mode="compose")
    calls = []

    def fail():
        raise RuntimeError("injected failure")

    deployment = SimpleNamespace(
        preflight=lambda _: calls.append("preflight"),
        stage=lambda _: calls.append("stage"),
        stop=lambda: calls.append("stop"),
        migrate=lambda _: fail(),
        switch=lambda _: calls.append("switch"),
        start=lambda _: calls.append("start"),
        healthy=lambda: calls.append("healthy"),
    )
    monkeypatch.setattr(releases, "require_host_administrator", lambda: None)
    monkeypatch.setattr(releases, "deployment_for", lambda _: deployment)
    monkeypatch.setattr(releases.ReleaseDownloader, "fetch_manifest", lambda *a: manifest)
    monkeypatch.setattr(releases, "database_revision", lambda _: "0001_v4_initial")
    monkeypatch.setattr(releases, "change_admission", lambda *a, **kw: calls.append("drain"))
    return installation, manifest, deployment, calls


def test_migration_failure_keeps_backup_and_does_not_switch(host, monkeypatch):
    installation, _, _, calls = host
    backup_id = str(uuid4())
    monkeypatch.setattr(releases.BackupStore, "create", lambda *a, **kw: backup_id)
    with pytest.raises(RuntimeError, match="requires attention"):
        releases.apply_release(installation, tag="v0.1.0rc23", request=lambda *a: None)
    journal = json.loads(next((installation.root / "operations").glob("*.json")).read_bytes())
    assert journal["phase"] == "backed_up"
    assert journal["backup_id"] == backup_id
    assert journal["failure_type"] == "RuntimeError"
    assert calls == ["preflight", "stage", "drain", "stop"]


def test_failed_restoration_leaves_services_stopped(host, monkeypatch):
    installation, manifest, _, calls = host
    operation_id = str(uuid4())
    releases.journal_path(installation.root, operation_id).write_text(
        json.dumps(
            {
                "id": operation_id,
                "phase": "backed_up",
                "backup_id": str(uuid4()),
                "previous": manifest.model_dump(mode="json"),
            }
        )
    )
    monkeypatch.setattr(
        releases.BackupStore, "verify", lambda *a: {"database_revision": "0001_v4_initial"}
    )

    def failure(*args, **kwargs):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(releases.BackupStore, "restore", failure)
    with pytest.raises(RuntimeError, match="restore failed"):
        releases.rollback_release(
            installation, operation_id=operation_id, acknowledge_data_loss=True
        )
    assert calls == ["stop"]
    assert releases.read_operation(installation.root, operation_id)["phase"] == "rollback_stopped"


def test_unmanaged_temporal_is_rejected_before_snapshot(tmp_path):
    installation = Installation(root=tmp_path, mode="systemd")
    with pytest.raises(ValueError, match="dedicated managed Temporal"):
        TemporalBackup(installation).validate()


def test_restoration_requires_explicit_data_loss_acknowledgement(host):
    installation, _, _, calls = host
    with pytest.raises(ValueError, match="acknowledge-data-loss"):
        releases.rollback_release(
            installation, operation_id=str(uuid4()), acknowledge_data_loss=False
        )
    assert calls == []


@pytest.mark.parametrize(
    "key",
    [
        "OPSMESH_DATABASE_URL",
        "OPSMESH_POSTGRES_PASSWORD",
        "OPSMESH_TEMPORAL_ADDRESS",
        "OPSMESH_TEMPORAL_NAMESPACE",
        "OPSMESH_TEMPORAL_DATABASE_URL",
        "OPSMESH_TEMPORAL_VISIBILITY_DATABASE_URL",
        "OPSMESH_TEMPORAL_POSTGRES_PASSWORD",
        "OPSMESH_REDIS_URL",
    ],
)
def test_restore_configuration_drift_is_rejected_before_database_io(tmp_path, key, monkeypatch):
    installation = Installation(root=tmp_path, mode="compose")
    store = BackupStore(installation)
    backup_id = str(uuid4())
    directory = store.path(backup_id)
    directory.mkdir(parents=True)
    (tmp_path / ".env").write_text(f"{key}=current-private-value\n")
    (directory / "configuration.env").write_text(f"{key}=saved-private-value\n")

    def unexpected_io(*args, **kwargs):
        pytest.fail("Configuration drift must be rejected before verification or database I/O")

    monkeypatch.setattr(store, "verify", unexpected_io)
    with pytest.raises(ValueError, match=f"differs for {key}") as error:
        store.restore(backup_id, acknowledge_data_loss=True)
    assert "private-value" not in str(error.value)
