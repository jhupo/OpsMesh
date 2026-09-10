from __future__ import annotations

import hashlib
import os
import re
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from opsmesh_operator.commands import run_command
from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest, require_tag
from opsmesh_operator.files import atomic_write


class ReleaseSource:
    def __init__(self, repository: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
            raise ValueError("Invalid release repository")
        self.repository = repository

    def fetch_manifest(self, tag: str, directory: Path) -> ReleaseManifest:
        require_tag(tag)
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            path = Path(temporary) / "release-manifest.json"
            self._download(tag, path.name, path, limit=1_000_000)
            # Authenticate bytes before interpreting their contents as deployment authority.
            self.verify(path, tag)
            manifest = ReleaseManifest.model_validate_json(path.read_bytes())
            if manifest.tag != tag or manifest.repository != self.repository:
                raise ValueError("Release identity mismatch")
            self.verify(path, tag, commit=manifest.commit)
            atomic_write(directory / path.name, manifest.model_dump_json(indent=2))
        return manifest

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(min=2, max=8),
        retry=retry_if_exception_type(RuntimeError),
        reraise=True,
    )
    def verify(self, path: Path, tag: str, *, commit: str | None = None) -> None:
        command = [
            "gh",
            "attestation",
            "verify",
            str(path),
            "--repo",
            self.repository,
            "--signer-workflow",
            f"{self.repository}/.github/workflows/release-publish.yml",
            "--source-ref",
            f"refs/tags/{require_tag(tag)}",
            "--deny-self-hosted-runners",
        ]
        if commit:
            command += ["--source-digest", commit]
        run_command(command, timeout=120)

    def download_file(self, manifest: ReleaseManifest, file: ReleaseFile, directory: Path) -> Path:
        path = directory / file.name
        self._download(manifest.tag, file.name, path, limit=file.size)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != file.size or digest != file.sha256:
            path.unlink()
            raise ValueError("Release artifact integrity check failed")
        return path

    def _download(self, tag: str, name: str, path: Path, *, limit: int) -> None:
        url = f"https://github.com/{self.repository}/releases/download/{tag}/{name}"
        deadline = time.monotonic() + 300
        with (
            httpx.Client(timeout=60, follow_redirects=True) as client,
            client.stream("GET", url) as response,
            path.open("wb") as stream,
        ):
            response.raise_for_status()
            total = 0
            for chunk in response.iter_bytes():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Release download exceeded its total time budget")
                total += len(chunk)
                if total > limit:
                    raise ValueError("Release download exceeds declared size")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())


def extract_bundle(bundle: Path, target: Path) -> None:
    """Reject links/devices and oversized or traversing archives before writing anything."""
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 50_000 or sum(member.size for member in members) > 1_000_000_000:
            raise ValueError("Release archive exceeds extraction limits")
        names: set[str] = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or ":" in member.name
                or member.name in names
            ):
                raise ValueError("Unsafe release archive member")
            names.add(member.name)
        archive.extractall(target, members=members, filter="data")
