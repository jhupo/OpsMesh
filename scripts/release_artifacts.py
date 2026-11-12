"""Shared archive and runtime inventory operations for release builders."""

import hashlib
import importlib.metadata
import tarfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def runtime_dependencies(name: str) -> list[dict[str, str]]:
    """Inventory the installed runtime closure, excluding build tools."""
    pending = [name]
    found: dict[str, dict[str, str]] = {}
    while pending:
        distribution = importlib.metadata.distribution(pending.pop())
        key = canonicalize_name(distribution.metadata["Name"])
        if key in found:
            continue
        found[key] = {
            "name": distribution.metadata["Name"],
            "version": distribution.version,
        }
        for value in distribution.requires or ():
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate(
                {"extra": ""}
            ):
                pending.append(requirement.name)
    return [found[key] for key in sorted(found)]


def create_archive(directory: Path, archive: Path) -> None:
    """Write a release archive and adjacent SHA256 checksum."""

    def owner(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        if info.isdir():
            info.mode = 0o755
        elif info.isfile():
            info.mode = 0o755 if info.mode & 0o111 else 0o644
        return info

    with tarfile.open(archive, "w:gz") as output:
        output.add(directory, arcname=archive.name[:-7], filter=owner)
    with archive.open("rb") as content:
        digest = hashlib.file_digest(content, "sha256").hexdigest()
    archive.with_name(archive.name[:-7] + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
