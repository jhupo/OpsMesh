"""Explicit host release changes with a journal readable while the API is stopped."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from opsmesh_operator.backups import BackupStore, database_revision
from opsmesh_operator.contracts import ReleaseManifest
from opsmesh_operator.deployments import deployment_for
from opsmesh_operator.files import atomic_write
from opsmesh_operator.installation import Installation
from opsmesh_operator.maintenance import maintenance_lock
from opsmesh_operator.releases import ReleaseDownloader


def require_host_administrator() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("Release changes require a Linux host administrator")


def journal_path(root: Path, operation_id: str) -> Path:
    return root / "operations" / f"{UUID(operation_id)}.json"


def read_operation(root: Path, operation_id: str) -> dict[str, object]:
    value = json.loads(journal_path(root, operation_id).read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Invalid host operation journal")
    return value


def apply_release(
    installation: Installation, *, tag: str, update_operator: bool = False
) -> dict[str, object]:
    require_host_administrator()
    operation_id = str(uuid4())
    path = journal_path(installation.root, operation_id)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    state: dict[str, object] = {"id": operation_id, "phase": "created", "tag": tag}

    def checkpoint(phase: str) -> None:
        state["phase"] = phase
        atomic_write(path, json.dumps(state, indent=2))

    with maintenance_lock(installation.root):
        previous = installation.current()
        state["previous"] = previous.model_dump(mode="json")
        checkpoint("preparing")
        try:
            manifest = ReleaseDownloader(installation.repository).fetch_manifest(
                tag, installation.root / "downloads" / tag
            )
            state["target"] = manifest.model_dump(mode="json")
            deployment = deployment_for(installation)
            deployment.preflight(manifest)
            operator_target = None
            if update_operator:
                from opsmesh_operator.updates import stage_operator

                state["previous_operator"] = str(
                    (installation.root / "operator").resolve(strict=True)
                )
                operator_target = stage_operator(installation, manifest)
            deployment.stage(manifest)
            from opsmesh_operator.temporal_backup import TemporalBackup

            TemporalBackup(installation).validate()
            revision = database_revision(installation)
            if (
                revision != manifest.database_revision
                and revision not in manifest.upgrade_from_revisions
            ):
                raise ValueError("The release does not support the current database revision")
            checkpoint("prepared")
            deployment.stop()
            checkpoint("stopped")
            if database_revision(installation) != revision:
                raise RuntimeError("The database revision changed while stopping services")
            state["backup_id"] = BackupStore(installation).create(database_revision=revision)
            checkpoint("backed_up")
            deployment.migrate(manifest)
            if database_revision(installation) != manifest.database_revision:
                raise RuntimeError("Migration did not reach the release's database revision")
            checkpoint("migrated")
            deployment.switch(manifest)
            checkpoint("switched")
            deployment.start(manifest)
            deployment.healthy()
            if operator_target is not None:
                from opsmesh_operator.updates import activate_operator

                activate_operator(installation.root, operator_target)
            checkpoint("ready")
            checkpoint("completed")
        except BaseException as exc:
            state["failure_type"] = type(exc).__name__
            checkpoint(str(state["phase"]))
            raise RuntimeError(
                f"Release change requires attention; host operation {operation_id}"
            ) from exc
    return state


def rollback_release(
    installation: Installation, *, operation_id: str, acknowledge_data_loss: bool
) -> dict[str, object]:
    require_host_administrator()
    if not acknowledge_data_loss:
        raise ValueError("Rollback requires --acknowledge-data-loss")
    with maintenance_lock(installation.root):
        state = read_operation(installation.root, operation_id)
        if state.get("phase") == "rolled_back":
            return state
        backup_id = state.get("backup_id")
        if not isinstance(backup_id, str):
            raise ValueError("The operation has no verified backup; inspect its host phase")
        previous = ReleaseManifest.model_validate(state["previous"])
        metadata = BackupStore(installation).verify(backup_id)
        if not previous.accepts_database_revision(str(metadata["database_revision"])):
            raise ValueError("The previous release cannot run against the backup schema")
        deployment = deployment_for(installation)
        deployment.stop()
        state["phase"] = "rollback_stopped"
        atomic_write(journal_path(installation.root, operation_id), json.dumps(state, indent=2))
        BackupStore(installation).restore(backup_id, acknowledge_data_loss=True)
        state["phase"] = "rollback_restored"
        atomic_write(journal_path(installation.root, operation_id), json.dumps(state, indent=2))
        deployment.switch(previous)
        deployment.start(previous)
        deployment.healthy()
        if isinstance(state.get("previous_operator"), str):
            from opsmesh_operator.updates import activate_operator

            activate_operator(installation.root, Path(str(state["previous_operator"])))
        state["phase"] = "rolled_back"
        atomic_write(journal_path(installation.root, operation_id), json.dumps(state, indent=2))
        return state
