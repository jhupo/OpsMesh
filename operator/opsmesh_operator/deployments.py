from __future__ import annotations

import json
import os
import platform
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from opsmesh_operator.commands import run_command
from opsmesh_operator.contracts import ReleaseManifest
from opsmesh_operator.files import atomic_write, sync_directory
from opsmesh_operator.installation import Installation
from opsmesh_operator.releases import ReleaseSource, extract_bundle


class Deployment(ABC):
    def __init__(self, installation: Installation) -> None:
        self.installation = installation
        self.root = installation.root

    def preflight(self, manifest: ReleaseManifest) -> None:
        from dotenv import dotenv_values

        settings = dotenv_values(self.root / ".env")
        if settings.get("OPSMESH_STORAGE_BACKEND", "local") != "local":
            raise ValueError("Managed upgrades require a coordinated local-storage backup")
        configured_storage = settings.get("OPSMESH_STORAGE_ROOT")
        if configured_storage and Path(configured_storage).resolve() != self.root / "data/storage":
            raise ValueError("Storage must be inside the managed persistent data directory")
        machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
        if platform.system() != "Linux" or f"linux/{machine}" not in manifest.platforms:
            raise ValueError("This release does not support the host platform")
        if shutil.disk_usage(self.root).free < 2_000_000_000:
            raise ValueError("At least 2 GB of free space is required before staging")
        if manifest.connector_protocol != 2:
            raise ValueError("This updater does not support the target connector protocol")
        run_command(["gh", "--version"], timeout=15)

    def stage(self, manifest: ReleaseManifest) -> Path:
        source = ReleaseSource(self.installation.repository)
        cache = self.root / "downloads" / manifest.tag
        cache.mkdir(parents=True, exist_ok=True)
        target = self.installation.release_dir(manifest.tag)
        if target.exists():
            existing = ReleaseManifest.model_validate_json(
                (target / "release-manifest.json").read_bytes()
            )
            if existing != manifest:
                raise ValueError("Immutable release directory already has different content")
            if not (target / ".prepared").exists():
                self.prepare(target, manifest)
                atomic_write(target / ".prepared", manifest.commit)
            return target
        record = next(
            file for file in manifest.files if file.name == f"opsmesh-server-{manifest.tag}.tar.gz"
        )
        archive = source.download_file(manifest, record, cache)
        staging = self.root / "releases" / f".{manifest.tag}.staging"
        # An interrupted stage is quarantined, never recursively deleted or reused.
        if staging.exists():
            raise RuntimeError("Interrupted staging directory requires operator inspection")
        extract_bundle(archive, staging)
        atomic_write(staging / "release-manifest.json", manifest.model_dump_json(indent=2))
        runtime_images = {manifest.image("runtime")}
        for path in (self.root / "releases").glob("v*/release-manifest.json"):
            retained = ReleaseManifest.model_validate_json(path.read_bytes())
            runtime_images.add(retained.image("runtime"))
        atomic_write(
            staging / "images.env",
            f"OPSMESH_BACKEND_IMAGE={manifest.image('backend')}\n"
            f"OPSMESH_RUNTIME_IMAGE={manifest.image('runtime')}\n"
            f"OPSMESH_RUNTIME_ALLOWED_IMAGES={json.dumps(sorted(runtime_images))}\n"
            f"OPSMESH_BUILD_COMMIT={manifest.commit}\n",
        )
        staging.rename(target)
        sync_directory(target.parent)
        self.prepare(target, manifest)
        atomic_write(target / ".prepared", manifest.commit)
        return target

    def switch(self, manifest: ReleaseManifest) -> None:
        target = self.installation.release_dir(manifest.tag)
        temporary = self.root / ".current.next"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, self.root / "current")
        sync_directory(self.root)

    def healthy(self) -> None:
        deadline = time.monotonic() + min(120, self.installation.timeout_seconds)
        with httpx.Client(timeout=5, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    if client.get(self.installation.health_url).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(2)
        raise RuntimeError("Application readiness check failed")

    @abstractmethod
    def prepare(self, directory: Path, manifest: ReleaseManifest) -> None: ...

    @abstractmethod
    def migrate(self, manifest: ReleaseManifest) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def start(self, manifest: ReleaseManifest) -> None: ...


class ComposeDeployment(Deployment):
    def command(self, directory: Path) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            "opsmesh",
            "--project-directory",
            str(self.root),
            "--env-file",
            str(self.root / ".env"),
            "--env-file",
            str(directory / "images.env"),
            "-f",
            str(directory / "deploy/server/compose.yml"),
        ]

    def prepare(self, directory: Path, manifest: ReleaseManifest) -> None:
        import docker

        client = docker.from_env(timeout=self.installation.timeout_seconds)
        try:
            client.ping()
            client.images.pull(manifest.image("backend"))
            client.images.pull(manifest.image("runtime"))
        finally:
            client.close()
        run_command(self.command(directory) + ["config", "--quiet"])

    def infrastructure(self, manifest: ReleaseManifest) -> None:
        run_command(
            self.command(self.installation.release_dir(manifest.tag))
            + ["up", "-d", "--wait", "postgres", "redis"]
        )

    def migrate(self, manifest: ReleaseManifest) -> None:
        run_command(
            self.command(self.installation.release_dir(manifest.tag))
            + ["run", "--rm", "--no-deps", "migrate"],
            timeout=self.installation.timeout_seconds,
        )

    def stop(self) -> None:
        run_command(self.command(self.root / "current") + ["stop", "api", "worker"])

    def start(self, manifest: ReleaseManifest) -> None:
        run_command(
            self.command(self.installation.release_dir(manifest.tag))
            + ["up", "-d", "--no-deps", "--wait", "api", "worker"],
            timeout=self.installation.timeout_seconds,
        )


class SystemdDeployment(Deployment):
    def prepare(self, directory: Path, manifest: ReleaseManifest) -> None:
        run_command(["uv", "sync", "--frozen", "--no-dev", "--no-editable"], cwd=directory)

    def migrate(self, manifest: ReleaseManifest) -> None:
        directory = self.installation.release_dir(manifest.tag)
        # Persistent configuration is linked, never copied into release assets.
        env_link = directory / ".env"
        if not env_link.exists():
            env_link.symlink_to(self.root / ".env")
        run_command([str(directory / ".venv/bin/alembic"), "upgrade", "head"], cwd=directory)

    def stop(self) -> None:
        run_command(["systemctl", "stop", "opsmesh-api", "opsmesh-worker"])

    def start(self, manifest: ReleaseManifest) -> None:
        run_command(["systemctl", "start", "opsmesh-api", "opsmesh-worker"])


def deployment_for(installation: Installation) -> Deployment:
    if installation.mode == "compose":
        return ComposeDeployment(installation)
    return SystemdDeployment(installation)
