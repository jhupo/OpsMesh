from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from opsmesh_operator.contracts import Contract, ReleaseManifest
from opsmesh_operator.files import atomic_write, require_install_root


class Installation(Contract):
    root: Path
    mode: Literal["compose", "systemd"]
    repository: str = "jhupo/OpsMesh"
    health_url: str = "http://127.0.0.1:8000/api/v1/health/ready"
    timeout_seconds: int = Field(default=900, ge=30, le=7200)

    @classmethod
    def load(cls, root: Path) -> Installation:
        root = require_install_root(root)
        result = cls.model_validate_json((root / "installation.json").read_bytes())
        if result.root != root:
            raise ValueError("Installation root mismatch")
        return result

    def save(self) -> None:
        require_install_root(self.root)
        atomic_write(self.root / "installation.json", self.model_dump_json(indent=2))

    def release_dir(self, tag: str) -> Path:
        from opsmesh_operator.contracts import require_tag

        return self.root / "releases" / require_tag(tag)

    def current(self) -> ReleaseManifest:
        return ReleaseManifest.model_validate_json(
            (self.root / "current" / "release-manifest.json").read_bytes()
        )
