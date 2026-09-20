from __future__ import annotations

import hashlib
import os
import re
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest, require_tag
from opsmesh_operator.files import atomic_write, sync_directory

_GITHUB_API_VERSION = "2022-11-28"
_DOWNLOAD_ATTEMPTS = 6
_DOWNLOAD_DEADLINE_SECONDS = 7200
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


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
            manifest = ReleaseManifest.model_validate_json(path.read_bytes())
            if manifest.tag != tag or manifest.repository != self.repository:
                raise ValueError("Release identity mismatch")
            atomic_write(directory / path.name, manifest.model_dump_json(indent=2))
        return manifest

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
        token = os.environ.get("OPSMESH_RELEASE_TOKEN")
        authorization = {"Authorization": f"Bearer {token}"} if token else {}
        metadata_headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
            **authorization,
        }
        asset_headers = {
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
            **authorization,
        }
        deadline = time.monotonic() + _DOWNLOAD_DEADLINE_SECONDS
        partial = path.with_name(f".{path.name}.part")
        if partial.exists() and partial.stat().st_size > limit:
            partial.unlink()
            raise ValueError("Partial release download exceeds declared size")
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            asset_url = self._asset_url(
                client,
                tag,
                name,
                metadata_headers,
                deadline=deadline,
            )
            for attempt in range(_DOWNLOAD_ATTEMPTS):
                offset = partial.stat().st_size if partial.exists() else 0
                headers = dict(asset_headers)
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                try:
                    with client.stream("GET", asset_url, headers=headers) as response:
                        response.raise_for_status()
                        append = offset > 0 and response.status_code == 206
                        if offset and not append:
                            offset = 0
                        total = offset
                        with partial.open("ab" if append else "wb") as stream:
                            for chunk in response.iter_bytes():
                                if time.monotonic() >= deadline:
                                    raise TimeoutError(
                                        "Release download exceeded its total time budget"
                                    )
                                total += len(chunk)
                                if total > limit:
                                    raise ValueError("Release download exceeds declared size")
                                stream.write(chunk)
                            stream.flush()
                            os.fsync(stream.fileno())
                except (httpx.HTTPError, OSError) as exc:
                    if not self._retryable(exc) or attempt + 1 == _DOWNLOAD_ATTEMPTS:
                        raise
                    self._wait_before_retry(attempt, deadline)
                    continue
                os.replace(partial, path)
                sync_directory(path.parent)
                return
        raise RuntimeError("Release download retry loop ended unexpectedly")

    def _asset_url(
        self,
        client: httpx.Client,
        tag: str,
        name: str,
        headers: dict[str, str],
        *,
        deadline: float,
    ) -> str:
        url = f"https://api.github.com/repos/{self.repository}/releases/tags/{tag}"
        release: dict[str, Any] | None = None
        for attempt in range(_DOWNLOAD_ATTEMPTS):
            try:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Release metadata is not an object")
                release = payload
                break
            except (httpx.HTTPError, ValueError) as exc:
                if not self._retryable(exc) or attempt + 1 == _DOWNLOAD_ATTEMPTS:
                    raise
                self._wait_before_retry(attempt, deadline)
        if release is None or release.get("tag_name") != tag:
            raise ValueError("Release identity mismatch")
        raw_assets = release.get("assets")
        if not isinstance(raw_assets, list):
            raise ValueError("Release assets are not a list")
        assets = [
            asset
            for asset in raw_assets
            if isinstance(asset, dict) and asset.get("name") == name
        ]
        if len(assets) != 1:
            raise ValueError("Release asset identity mismatch")
        asset_url_value = assets[0].get("url")
        if not isinstance(asset_url_value, str):
            raise ValueError("Release asset identity mismatch")
        asset_url: str = asset_url_value
        expected_prefix = f"https://api.github.com/repos/{self.repository}/releases/assets/"
        if not asset_url.startswith(expected_prefix) or not asset_url.removeprefix(
            expected_prefix
        ).isdigit():
            raise ValueError("Release asset endpoint is outside the repository")
        return asset_url

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        return not isinstance(exc, httpx.HTTPStatusError) or (
            exc.response.status_code in _RETRYABLE_STATUS_CODES
        )

    @staticmethod
    def _wait_before_retry(attempt: int, deadline: float) -> None:
        delay = min(2**attempt, 10)
        if time.monotonic() + delay >= deadline:
            raise TimeoutError("Release download exceeded its total time budget")
        time.sleep(delay)


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
