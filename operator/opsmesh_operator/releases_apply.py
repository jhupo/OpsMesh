"""Explicit host release changes with a journal readable while the API is stopped."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

from opsmesh_operator.backups import BackupStore, database_revision
from opsmesh_operator.contracts import ReleaseManifest
from opsmesh_operator.deployments import deployment_for
from opsmesh_operator.files import atomic_write
from opsmesh_operator.installation import Installation
from opsmesh_operator.maintenance import maintenance_lock
from opsmesh_operator.releases import ReleaseDownloader

AdminRequest = Callable[[str, str, dict[str, object] | None], object]


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


def change_admission(
    request: AdminRequest, *, drained: bool, operation_id: str, timeout: int
) -> None:
    command = "admission.drain" if drained else "admission.resume"
    result = request(
        "POST",
        "/platform/operations",
        {
            "owner": "platform",
            "command": command,
            "target": {},
            "idempotency_key": f"host:{operation_id}:{command}",
        },
    )
    if not isinstance(result, dict) or "id" not in result:
        raise RuntimeError("The API returned an invalid admission receipt")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = request("GET", f"/platform/operations/{result['id']}", None)
        if not isinstance(status, dict):
            raise RuntimeError("The API returned an invalid operation receipt")
        if status.get("status") == "completed":
            current = request("GET", "/platform/operations/admission", None)
            if not isinstance(current, dict) or current.get("drained") != drained:
                raise RuntimeError("The admission state does not match the requested command")
            active = current.get("active")
            if not drained or (
                isinstance(active, dict) and all(value == 0 for value in active.values())
            ):
                return
        elif status.get("status") in {"cancelled", "needs_attention"}:
            raise RuntimeError("The admission command requires operator attention")
        time.sleep(1)
    raise RuntimeError("Admission did not drain before the deadline; services were not stopped")


def apply_release(
    installation: Installation, *, tag: str, request: AdminRequest
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
            change_admission(
                request,
                drained=True,
                operation_id=operation_id,
                timeout=installation.timeout_seconds,
            )
            checkpoint("drained")
            deployment.stop()
            checkpoint("stopped")
            if database_revision(installation) != revision:
                raise RuntimeError("The database revision changed during admission drain")
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
            checkpoint("ready")
            change_admission(
                request,
                drained=False,
                operation_id=operation_id,
                timeout=installation.timeout_seconds,
            )
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
        state["phase"] = "rolled_back"
        # The snapshot retains drained admission. Resume is an explicit administrator API command.
        atomic_write(journal_path(installation.root, operation_id), json.dumps(state, indent=2))
        return state
