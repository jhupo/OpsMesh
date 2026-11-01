"""Download verified Linux releases and reuse the existing installation transaction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx
from packaging.version import Version

from opsmesh import __version__
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract

REPOSITORY = "jhupo/OpsMesh"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_STABLE_VERSION = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}")


class ReleaseUpdate(OpsMeshContract):
    version: str
    current_version: str = __version__
    available: bool
    asset_name: str
    download_url: str
    sha256: str


class ReleaseUpdateService:
    """Repository and install commands are fixed; callers select only a release version."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    @staticmethod
    def _url(url: str) -> str:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise OpsMeshError("UPDATE_INVALID", "Release download location is invalid") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in {
                "github.com",
                "api.github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }
            or parsed.username
            or parsed.password
            or port not in {None, 443}
        ):
            raise OpsMeshError("UPDATE_INVALID", "Release download location is invalid")
        return url

    async def _download(self, url: str, destination: Path, maximum: int) -> None:
        try:
            await self._stream_download(url, destination, maximum)
        except httpx.HTTPError:
            raise OpsMeshError(
                "UPDATE_UNAVAILABLE", "Release service unavailable", retryable=True
            ) from None
        except OSError:
            raise OpsMeshError("UPDATE_STORAGE_FAILED", "Cannot write release files") from None

    async def _stream_download(self, url: str, destination: Path, maximum: int) -> None:
        for _ in range(6):
            async with self.client.stream(
                "GET", self._url(url), follow_redirects=False
            ) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers.get("location", "")))
                    continue
                if response.status_code != 200:
                    raise OpsMeshError(
                        "UPDATE_UNAVAILABLE", "Release download failed", retryable=True
                    )
                size = 0
                with destination.open("xb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > maximum:
                            raise OpsMeshError("UPDATE_INVALID", "Release asset exceeds size limit")
                        output.write(chunk)
                return
        raise OpsMeshError("UPDATE_INVALID", "Too many release redirects")

    async def check(self, version: str | None = None) -> ReleaseUpdate:
        if version is not None and not _STABLE_VERSION.fullmatch(version.removeprefix("v")):
            raise OpsMeshError("INVALID_REQUEST", "Choose a stable release version such as 0.3.2")
        selector = "latest" if version is None else "tags/v" + version.removeprefix("v")
        with tempfile.TemporaryDirectory(prefix="opsmesh-release-") as temporary:
            metadata = Path(temporary) / "release.json"
            await self._download(
                f"https://api.github.com/repos/{REPOSITORY}/releases/{selector}",
                metadata,
                2 * 1024 * 1024,
            )
            try:
                release = json.loads(metadata.read_bytes())
                tag = release["tag_name"]
                if (
                    release.get("draft")
                    or release.get("prerelease")
                    or not isinstance(tag, str)
                    or not tag.startswith("v")
                    or not _STABLE_VERSION.fullmatch(tag[1:])
                ):
                    raise ValueError("Not stable")
                resolved = tag[1:]
                if version and resolved != version.removeprefix("v"):
                    raise ValueError("Release identity differs")
                name = f"opsmesh-{resolved}-linux.tar.gz"
                assets = {item["name"]: item for item in release["assets"]}
                asset = assets[name]
                url = self._url(asset["browser_download_url"])
                expected_path = f"/{REPOSITORY}/releases/download/{tag}/{name}"
                if urlsplit(url).hostname != "github.com" or urlsplit(url).path != expected_path:
                    raise ValueError("Release asset identity differs")
                digest = asset.get("digest") or ""
                if digest.startswith("sha256:"):
                    digest = digest.removeprefix("sha256:")
                else:
                    checksum = Path(temporary) / "checksum"
                    checksum_url = assets[name + ".sha256"]["browser_download_url"]
                    if checksum_url != url + ".sha256":
                        raise ValueError("Checksum asset identity differs")
                    await self._download(checksum_url, checksum, 4096)
                    fields = checksum.read_text().strip().split()
                    if len(fields) != 2 or fields[1].lstrip("*") != name:
                        raise ValueError("Checksum identity differs")
                    digest = fields[0]
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("Missing digest")
            except (ValueError, KeyError, TypeError, AttributeError):
                raise OpsMeshError(
                    "UPDATE_INVALID", "Release lacks a valid Linux asset and checksum"
                ) from None
        return ReleaseUpdate(
            version=resolved,
            available=Version(resolved) > Version(__version__),
            asset_name=name,
            download_url=url,
            sha256=digest,
        )

    async def download(self, release: ReleaseUpdate, directory: Path) -> Path:
        name = f"opsmesh-{release.version}-linux.tar.gz"
        if (
            not _STABLE_VERSION.fullmatch(release.version)
            or release.asset_name != name
            or release.download_url
            != f"https://github.com/{REPOSITORY}/releases/download/v{release.version}/{name}"
            or not re.fullmatch(r"[0-9a-f]{64}", release.sha256)
        ):
            raise OpsMeshError("UPDATE_INVALID", "Release identity is invalid")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
            candidate = Path(temporary) / release.asset_name
            await self._download(release.download_url, candidate, MAX_ARCHIVE_BYTES)
            with candidate.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != release.sha256:
                raise OpsMeshError("UPDATE_INVALID", "Downloaded release checksum differs")
            destination = directory / release.asset_name
            os.replace(candidate, destination)
        return destination

    @staticmethod
    def extract(archive: Path, directory: Path, version: str) -> Path:
        if not _STABLE_VERSION.fullmatch(version):
            raise OpsMeshError("UPDATE_INVALID", "Release version is invalid")
        if directory.is_symlink() or (directory.exists() and any(directory.iterdir())):
            raise OpsMeshError("UPDATE_INVALID", "Extraction directory must be empty")
        try:
            return ReleaseUpdateService._extract(archive, directory, version)
        except (OSError, ValueError, tarfile.TarError):
            raise OpsMeshError("UPDATE_INVALID", "Release archive is invalid") from None

    @staticmethod
    def _extract(archive: Path, directory: Path, version: str) -> Path:
        root_name = f"opsmesh-{version}-linux"
        with tarfile.open(archive) as bundle:
            members: list[tarfile.TarInfo] = []
            total_size = 0
            names: set[str] = set()
            for member in bundle:
                total_size += member.size
                if len(members) >= 1000 or total_size > 512 * 1024 * 1024:
                    raise OpsMeshError(
                        "UPDATE_INVALID", "Release archive exceeds extraction limits"
                    )
                member_path = PurePosixPath(member.name)
                if (
                    not member_path.parts
                    or member_path.parts[0] != root_name
                    or member_path.is_absolute()
                    or ".." in member_path.parts
                    or "\\" in member.name
                    or ":" in member.name
                    or not (member.isfile() or member.isdir())
                    or member_path.as_posix() in names
                ):
                    raise OpsMeshError("UPDATE_INVALID", "Release archive contains an unsafe entry")
                names.add(member_path.as_posix())
                members.append(member)
            # Entries have been checked explicitly for Python 3.11 compatibility.
            bundle.extractall(directory, members=members, filter="data")
        root = directory / root_name
        if (root / "VERSION").read_text().strip() != version:
            raise OpsMeshError("UPDATE_INVALID", "Release version file differs")
        manifest = (root / "SHA256SUMS").read_text().splitlines()
        expected: set[str] = set()
        for line in manifest:
            digest, name = line.split("  ", 1)
            path = root / name
            if (
                not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file()
                or path.is_symlink()
                or name in expected
            ):
                raise OpsMeshError("UPDATE_INVALID", "Release manifest is invalid")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise OpsMeshError("UPDATE_INVALID", "Release content checksum differs")
            expected.add(name)
        actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
        if actual != expected | {"SHA256SUMS"} or "install.sh" not in expected:
            raise OpsMeshError("UPDATE_INVALID", "Release manifest does not cover all files")
        return root

    async def install(
        self,
        release: ReleaseUpdate,
        directory: Path,
        *,
        instance: str,
        mode: str = "local",
        no_start: bool = False,
    ) -> None:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise OpsMeshError("FORBIDDEN", "Install updates from a root Linux maintenance shell")
        if not release.available or Version(release.version) <= Version(__version__):
            raise OpsMeshError(
                "CONFLICT", "The selected version is not newer than this installation"
            )
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}[a-z0-9]|[a-z]", instance) or mode not in {
            "local",
            "docker",
        }:
            raise OpsMeshError("INVALID_REQUEST", "Installation instance or mode is invalid")
        installed = self.installed_version(instance)
        if Version(release.version) <= Version(installed):
            raise OpsMeshError("CONFLICT", "Selected release is not newer than the target instance")
        archive = await self.download(release, directory)
        with tempfile.TemporaryDirectory(prefix="install-", dir=directory) as temporary:
            root = self.extract(archive, Path(temporary), release.version)
            arguments = [
                "bash",
                str(root / "install.sh"),
                "--upgrade",
                "--mode",
                mode,
                "--instance",
                instance,
                "--expect-installed-version",
                installed,
            ]
            if no_start:
                arguments.append("--no-start")
            process = await asyncio.create_subprocess_exec(*arguments)
            if await process.wait() != 0:
                raise OpsMeshError(
                    "UPDATE_INSTALL_FAILED", "Installer failed; inspect the maintenance logs"
                )

    @staticmethod
    def installed_version(instance: str) -> str:
        try:
            version = (Path("/opt") / instance / "current" / "VERSION").read_text().strip()
            Version(version)
            return version
        except (OSError, ValueError):
            raise OpsMeshError("UPDATE_INVALID", "Target instance version is unavailable") from None
