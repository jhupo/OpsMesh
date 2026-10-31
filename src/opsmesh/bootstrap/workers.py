"""Bind application settings to the independently implemented worker launcher."""

from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers import (
    RunWorkerLauncher,
    WorkerLaunchConfiguration,
    WorkerMode,
)
from opsmesh.infrastructure.run_workers.kubernetes.client import KubernetesAPI

from .settings import OpsMeshSettings


def worker_settings_payload(settings: OpsMeshSettings, mode: WorkerMode) -> str:
    data = settings.model_dump(mode="json")
    data["worker_enabled"] = False
    data["database_url"] = settings.resolved_database_url
    data["data_directory"] = str(settings.data_directory.resolve())
    if mode == "docker":
        url = make_url(data["database_url"])
        if url.drivername.startswith("sqlite"):
            path = Path(url.database or "").resolve()
            try:
                relative = path.relative_to(settings.data_directory.resolve())
            except ValueError as error:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Container SQLite must reside in the mounted data directory"
                ) from error
            data["database_url"] = str(url.set(database=f"/var/lib/opsmesh/{relative.as_posix()}"))
        data["data_directory"] = "/var/lib/opsmesh"
    return OpsMeshSettings.model_validate(data).model_dump_json()


def create_run_worker_launcher(
    runtime: Any,
    settings: OpsMeshSettings,
    *,
    mode: WorkerMode = "inline",
    docker_image: str | None = None,
    cpu: float = 1.0,
    memory_mb: int = 512,
    pids_limit: int = 128,
    network: str = "bridge",
    workdir: Path | None = None,
    environment_allowlist: tuple[str, ...] = (),
    kubernetes_api: KubernetesAPI | None = None,
) -> RunWorkerLauncher:
    configuration = WorkerLaunchConfiguration(
        data_directory=settings.data_directory,
        database_url=settings.resolved_database_url,
        kubernetes_worker=settings.kubernetes_worker,
        settings_payload=lambda worker_mode: worker_settings_payload(settings, worker_mode),
    )
    return RunWorkerLauncher(
        runtime,
        configuration,
        mode=mode,
        docker_image=docker_image,
        cpu=cpu,
        memory_mb=memory_mb,
        pids_limit=pids_limit,
        network=network,
        workdir=workdir,
        environment_allowlist=environment_allowlist,
        kubernetes_api=kubernetes_api,
    )
