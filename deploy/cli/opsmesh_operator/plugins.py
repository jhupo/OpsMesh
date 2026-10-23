"""Offline package preparation; running hosts observe registry updates independently."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from filelock import FileLock
from opsmesh_sdk.packages import MAX_PACKAGE_SIZE, build_package, extract_package, inspect_package

from opsmesh_operator.files import atomic_write
from opsmesh_operator.installation import Installation
from opsmesh_operator.maintenance import maintenance_lock
from opsmesh_operator.releases_apply import require_host_administrator


def registry(root: Path) -> dict[str, Any]:
    path = root / "plugins/registry.json"
    value = json.loads(path.read_bytes()) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError("Invalid plugin registry")
    return value


def save_registry(root: Path, value: dict[str, Any]) -> None:
    path = root / "plugins/registry.json"
    atomic_write(path, json.dumps(value, indent=2), mode=0o640)
    os.chown(path, 0, 10001)


def install_plugin(
    installation: Installation,
    source: str,
    sha256: str | None,
) -> dict[str, Any]:
    require_host_administrator()
    root = installation.root / "plugins"
    root.mkdir(exist_ok=True, mode=0o755)
    os.chown(root, 10001, 10001)
    root.chmod(0o2750)
    with (
        maintenance_lock(installation.root),
        FileLock(installation.root / "operator.lock", timeout=0),
        FileLock(root / ".registry.lock", timeout=30, mode=0o660),
        tempfile.TemporaryDirectory(dir=root) as temporary,
    ):
        work = Path(temporary)
        if source.startswith("https://"):
            url = urlsplit(source)
            if url.username or url.password or url.fragment or not sha256:
                raise ValueError("Online plugin install requires SHA-256 and a credential-free URL")
            archive = work / "package.zip"

            def require_https(request: httpx.Request) -> None:
                if request.url.scheme != "https" or request.url.username or request.url.password:
                    raise ValueError("Plugin downloads require HTTPS without URL credentials")

            with (
                httpx.Client(
                    timeout=60, follow_redirects=True, event_hooks={"request": [require_https]}
                ) as client,
                client.stream("GET", source) as response,
                archive.open("xb") as output,
            ):
                response.raise_for_status()
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_PACKAGE_SIZE:
                        raise ValueError("Plugin download exceeds 50 MB")
                    output.write(chunk)
        else:
            local = Path(source).resolve(strict=True)
            if local.is_file() and local.stat().st_size > MAX_PACKAGE_SIZE:
                raise ValueError("Plugin ZIP exceeds 50 MB")
            archive = work / "package.zip"
            if local.is_dir():
                build_package(local, archive)
            else:
                shutil.copyfile(local, archive)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha256 and digest != sha256.lower():
            raise ValueError("Plugin checksum mismatch")
        manifest = inspect_package(archive)
        target = root / manifest.id / manifest.version
        if target.exists():
            raise ValueError("Plugin versions are immutable; choose a new version")
        staged = work / "unpacked"
        extract_package(archive, staged)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(target)
        target.chmod(0o755)
        target.parent.chmod(0o755)
        os.chown(target.parent, 10001, 10001)
        for path in target.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        value = registry(installation.root)
        previous = value.get(manifest.id, {})
        value[manifest.id] = {
            "manifest": manifest.model_dump(),
            "sha256": digest,
            "enabled": False,
            **{
                key: previous[key]
                for key in ("config", "encrypted_config", "config_key_id")
                if key in previous
            },
        }
        save_registry(installation.root, value)
    return {
        "id": manifest.id,
        "version": manifest.version,
        "enabled": False,
        "reload_pending": True,
    }


def configure_plugin(
    installation: Installation, plugin_id: str, *, enabled: bool, config: Path | None = None
) -> dict[str, Any]:
    require_host_administrator()
    with (
        maintenance_lock(installation.root),
        FileLock(installation.root / "operator.lock", timeout=0),
        FileLock(installation.root / "plugins/.registry.lock", timeout=30, mode=0o660),
    ):
        value = registry(installation.root)
        if plugin_id not in value:
            raise ValueError("Plugin is not installed")
        record = value[plugin_id]
        if config is not None:
            settings = json.loads(config.read_bytes())
            if not isinstance(settings, dict):
                raise ValueError("Plugin configuration must be an object")
            record["config"] = settings
            record.pop("encrypted_config", None)
            record.pop("config_key_id", None)
        record["enabled"] = enabled
        save_registry(installation.root, value)
    return {"id": plugin_id, "enabled": enabled, "reload_pending": True}


def uninstall_plugin(installation: Installation, plugin_id: str) -> dict[str, Any]:
    require_host_administrator()
    with (
        maintenance_lock(installation.root),
        FileLock(installation.root / "operator.lock", timeout=0),
        FileLock(installation.root / "plugins/.registry.lock", timeout=30, mode=0o660),
    ):
        value = registry(installation.root)
        if plugin_id not in value:
            raise ValueError("Plugin is not installed")
        del value[plugin_id]
        save_registry(installation.root, value)
    # Loaded modules may still read their files until the service has stopped.
    return {"id": plugin_id, "removed": True, "reload_pending": True, "files_retained": True}
