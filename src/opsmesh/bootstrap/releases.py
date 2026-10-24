"""Download immutable Linux bundles from a configured GitHub repository."""

import hashlib
import json
import platform
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from opsmesh.shared.errors import InvalidArgumentError

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise InvalidArgumentError("Release repository must be owner/repository")
    return value


def linux_platform() -> str:
    machine = platform.machine()
    if platform.system() != "Linux" or machine not in {"x86_64", "aarch64"}:
        raise InvalidArgumentError("Release installation requires Linux x86_64 or aarch64")
    return "linux-" + machine


def manifest(bundle: Path) -> dict[str, Any]:
    try:
        value = json.loads((bundle / "release.json").read_text())
    except (OSError, ValueError) as exc:
        raise InvalidArgumentError("Release manifest is missing or invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("format") != 1
        or not isinstance(value.get("version"), str)
        or not VERSION.fullmatch(value["version"])
        or value.get("platform") != linux_platform()
        or not (bundle / "bin/opsmesh").is_file()
        or not (bundle / "systemd/opsmesh.target").is_file()
    ):
        raise InvalidArgumentError("Release manifest, executable or platform is invalid")
    return value


def latest_release(client: httpx.Client, repository: str) -> dict[str, Any]:
    response = client.get(
        f"https://api.github.com/repos/{repository_name(repository)}/releases/latest"
    )
    response.raise_for_status()
    release = response.json()
    if not isinstance(release, dict):
        raise InvalidArgumentError("Invalid release response")
    tag = release.get("tag_name", "")
    if (
        release.get("draft")
        or release.get("prerelease")
        or not isinstance(tag, str)
        or not tag.startswith("v")
        or not VERSION.fullmatch(tag[1:])
    ):
        raise InvalidArgumentError("Latest release must have a stable vMAJOR.MINOR.PATCH tag")
    return release


def download_release(
    client: httpx.Client, repository: str, release: dict[str, Any], staging: Path
) -> Path:
    tag = release["tag_name"]
    asset = f"opsmesh-{tag}-{linux_platform()}.tar.gz"
    assets = {item["name"]: item["browser_download_url"] for item in release["assets"]}
    if asset not in assets or "SHA256SUMS" not in assets:
        raise InvalidArgumentError("Release has no bundle or checksums for this platform")
    base = f"https://github.com/{repository_name(repository)}/releases/download/{quote(tag)}/"
    if assets[asset] != base + asset or assets["SHA256SUMS"] != base + "SHA256SUMS":
        raise InvalidArgumentError("Release asset URLs do not match the configured repository")
    response = client.get(assets["SHA256SUMS"])
    response.raise_for_status()
    if len(response.content) > 64000:
        raise InvalidArgumentError("Release checksums exceed the size limit")
    hashes = {}
    for line in response.text.splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            raise InvalidArgumentError("Invalid release checksums")
        expected, name = fields
        name = name.lstrip(" *")
        if name in hashes or not re.fullmatch("[0-9a-f]{64}", expected):
            raise InvalidArgumentError("Invalid release checksums")
        hashes[name] = expected
    if asset not in hashes:
        raise InvalidArgumentError("Bundle checksum is missing")
    archive_path = staging / asset
    digest = hashlib.sha256()
    total = 0
    with client.stream("GET", assets[asset]) as response, archive_path.open("xb") as target:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > 2_000_000_000:
                raise InvalidArgumentError("Release bundle exceeds the size limit")
            digest.update(chunk)
            target.write(chunk)
    if digest.hexdigest() != hashes[asset]:
        raise InvalidArgumentError("Release checksum mismatch")
    destination = staging / "unpacked"
    destination.mkdir()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 100_000 or sum(item.size for item in members) > 4_000_000_000:
            raise InvalidArgumentError("Expanded release exceeds its size or entry limit")
        archive.extractall(destination, members=members, filter="data")
    bundle = destination / "opsmesh"
    if manifest(bundle)["version"] != tag[1:]:
        raise InvalidArgumentError("Release manifest and tag do not match")
    return bundle


def copy_bundle(source: Path, releases: Path) -> Path:
    version = manifest(source)["version"]
    destination = releases / version
    if destination.exists():
        raise InvalidArgumentError("This release directory already exists; preserve it for review")
    shutil.copytree(source, destination, symlinks=True)
    return destination


def release_client() -> httpx.Client:
    # Redirects are required for GitHub's release-asset CDN. Only public assets are used.
    return httpx.Client(
        timeout=httpx.Timeout(300, connect=15),
        follow_redirects=True,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "OpsMesh-Updater"},
    )
