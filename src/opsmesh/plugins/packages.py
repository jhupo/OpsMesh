"""One package store for online installation and generation coordination."""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from filelock import FileLock
from opsmesh_sdk import PluginManifest
from opsmesh_sdk.packages import extract_package, inspect_package


class PluginPackages:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def lock(self) -> FileLock:
        return FileLock(self.root / ".registry.lock", timeout=30, mode=0o660)

    def read(self) -> dict[str, Any]:
        path = self.root / "registry.json"
        result = json.loads(path.read_bytes()) if path.exists() else {}
        if not isinstance(result, dict):
            raise ValueError("Plugin registry must be an object")
        for plugin_id, record in result.items():
            if not isinstance(record, dict):
                raise ValueError("Invalid plugin installation record")
            manifest = PluginManifest.model_validate(record["manifest"])
            if manifest.id != plugin_id or not isinstance(record.get("config", {}), dict):
                raise ValueError("Invalid plugin installation identity or configuration")
        return result

    def write(self, records: dict[str, Any]) -> None:
        path = self.root / f".registry-{uuid4().hex}.tmp"
        try:
            with path.open("x", encoding="utf-8") as stream:
                os.chmod(path, 0o640)
                json.dump(records, stream, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            path.replace(self.root / "registry.json")
        finally:
            path.unlink(missing_ok=True)

    def install(
        self,
        archive: Path,
        sha256: str | None = None,
        *,
        validate: Callable[[PluginManifest, Path], None],
    ) -> PluginManifest:
        manifest = inspect_package(archive)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha256 is not None and digest != sha256.lower():
            raise ValueError("Plugin checksum mismatch")
        parent = self.root / manifest.id
        if parent.is_symlink():
            raise ValueError("Plugin parent cannot be a symlink")
        target = parent / manifest.version
        with TemporaryDirectory(dir=self.root) as temporary:
            staged = Path(temporary) / "package"
            extract_package(archive, staged)
            validate(manifest, staged)
            staged.chmod(0o750)
            for path in staged.rglob("*"):
                if path.is_symlink():
                    raise ValueError("Installed packages cannot contain symlinks")
                path.chmod(0o750 if path.is_dir() else 0o640)
            if target.exists():
                if target.is_symlink() or self.contents(target) != self.contents(staged):
                    raise ValueError("Plugin versions are immutable; choose a new version")
                return manifest
            parent.mkdir(exist_ok=True, mode=0o750)
            staged.rename(target)
        return manifest

    @staticmethod
    def contents(directory: Path) -> dict[str, str]:
        result = {}
        for path in directory.rglob("*"):
            relative = path.relative_to(directory)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise ValueError("Installed packages cannot contain symlinks")
            if path.is_file():
                with path.open("rb") as stream:
                    result[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        return result
