from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from backend.app.admin.releases.models import ReleaseAsset, ReleaseUpdateCheck, ReleaseVersion
from backend.app.admin.releases.versioning import normalize_release_tag, version_tuple
from backend.app.core.config import Settings


class GitHubReleaseClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch_latest(self, current: ReleaseVersion) -> ReleaseUpdateCheck:
        api_url = self._latest_release_url()
        with httpx.Client(timeout=self._settings.release_update_http_timeout_seconds) as client:
            response = client.get(
                api_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "OpsMesh-updater",
                },
            )
            response.raise_for_status()
            payload = response.json()
        latest_tag = normalize_release_tag(str(payload["tag_name"]))
        latest = ReleaseVersion(
            version=latest_tag.removeprefix("v"),
            tag=latest_tag,
            commit=_release_commit(payload),
        )
        return ReleaseUpdateCheck(
            current=current,
            latest=latest,
            update_available=version_tuple(latest.tag) > version_tuple(current.tag),
            release_url=str(payload.get("html_url") or ""),
            assets=[_asset_from_payload(item) for item in payload.get("assets", [])],
            cached=False,
        )

    def _latest_release_url(self) -> str:
        repository = self._settings.release_update_repository.strip()
        if not _is_repository_name(repository):
            raise ValueError("Release update repository must look like owner/repo")
        base_url = self._settings.release_update_github_api_url.rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https":
            raise ValueError("Release update GitHub API URL must use HTTPS")
        return f"{base_url}/repos/{repository}/releases/latest"


def _is_repository_name(value: str) -> bool:
    parts = value.split("/")
    if len(parts) != 2:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
    return all(part and set(part) <= allowed for part in parts)


def _asset_from_payload(payload: object) -> ReleaseAsset:
    if not isinstance(payload, dict):
        raise ValueError("Invalid release asset payload")
    return ReleaseAsset(
        name=str(payload.get("name") or ""),
        browser_download_url=str(payload.get("browser_download_url") or ""),
        size=int(payload.get("size") or 0),
        content_type=str(payload["content_type"]) if payload.get("content_type") else None,
        digest=str(payload["digest"]) if payload.get("digest") else None,
    )


def _release_commit(payload: dict[str, object]) -> str | None:
    target = payload.get("target_commitish")
    if isinstance(target, str) and target:
        return target
    return None
