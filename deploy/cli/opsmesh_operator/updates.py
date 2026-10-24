"""Online version discovery and versioned native operator activation."""

import json
import os
import platform
from pathlib import Path

from packaging.version import Version

from opsmesh_operator.contracts import ReleaseManifest
from opsmesh_operator.files import sync_directory
from opsmesh_operator.installation import Installation
from opsmesh_operator.releases import ReleaseDownloader, extract_bundle


def check_update(installation: Installation, tag: str | None = None) -> dict[str, object]:
    selected = tag or ReleaseDownloader(installation.repository).latest_tag()
    from opsmesh_operator.contracts import require_tag

    require_tag(selected)
    current = installation.current().tag
    return {
        "current": current,
        "available": selected,
        "update_available": Version(selected.removeprefix("v"))
        > Version(current.removeprefix("v")),
    }


def stage_operator(installation: Installation, manifest: ReleaseManifest) -> Path:
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    name = f"opsmesh-cli-{manifest.tag}-linux-{architecture}.tar.gz"
    record = next(file for file in manifest.files if file.name == name)
    root = installation.root
    target = root / "operator-releases" / f"{manifest.tag}-{manifest.commit}"
    target.parent.mkdir(exist_ok=True, mode=0o755)
    if not target.exists():
        source = ReleaseDownloader(installation.repository)
        archive = source.download_file(manifest, record, root / "downloads" / manifest.tag)
        staged = target.with_name(f".{target.name}.staging")
        extract_bundle(archive, staged)
        staged.chmod(0o755)
        staged.rename(target)
    identity = json.loads((target / "BUILD.json").read_bytes())
    if (
        identity.get("tag") != manifest.tag
        or identity.get("commit") != manifest.commit
        or identity.get("kind") != "cli"
        or identity.get("platform") != f"linux-{architecture}"
        or identity.get("format") != "pyinstaller-onedir"
    ):
        raise ValueError("Operator build identity mismatch")
    if not (target / "opsmesh").is_file():
        raise ValueError("Native operator executable is missing")
    return target


def activate_operator(root: Path, target: Path) -> None:
    if (
        target.parent != root / "operator-releases"
        or not target.is_dir()
        or target.is_symlink()
        or target.resolve().parent != (root / "operator-releases").resolve()
    ):
        raise ValueError("Operator target must be a staged release")
    temporary = root / ".operator.next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target, target_is_directory=True)
    os.replace(temporary, root / "operator")
    sync_directory(root)
