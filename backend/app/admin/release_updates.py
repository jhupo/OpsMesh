from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit

import httpx

from backend.app.core.config import Settings

RELEASE_TAG_PATTERN = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_LATEST_CACHE: dict[str, tuple[float, ReleaseUpdateCheck]] = {}


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    browser_download_url: str
    size: int
    content_type: str | None
    digest: str | None = None


@dataclass(frozen=True)
class ReleaseVersion:
    version: str
    tag: str
    commit: str | None = None


@dataclass(frozen=True)
class ReleaseUpdateCheck:
    current: ReleaseVersion
    latest: ReleaseVersion | None
    update_available: bool
    release_url: str | None
    assets: list[ReleaseAsset]
    cached: bool


@dataclass(frozen=True)
class ReleaseUpdatePlan:
    action: str
    tag: str | None
    manifest_url: str | None
    manifest_file: str | None
    bundle_url: str | None
    bundle_file: str | None
    checksum_url: str | None
    checksum_file: str | None
    release_dir: str | None
    command: list[str]
    dry_run: bool


@dataclass(frozen=True)
class ReleaseUpdateStart:
    action: str
    tag: str | None
    manifest_url: str | None
    manifest_file: str | None
    bundle_url: str | None
    bundle_file: str | None
    checksum_url: str | None
    checksum_file: str | None
    release_dir: str | None
    command: list[str]
    dry_run: bool
    started: bool
    pid: int | None


class ReleaseUpdateService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def current_version(self) -> ReleaseVersion:
        try:
            package_version = version("opsmesh")
        except PackageNotFoundError:
            package_version = "0.1.0"
        return ReleaseVersion(
            version=package_version,
            tag=f"v{package_version}",
            commit=os.environ.get("OPSMESH_BUILD_COMMIT"),
        )

    def check_updates(self, *, force: bool = False) -> ReleaseUpdateCheck:
        cache_key = self._settings.release_update_repository
        now = time.monotonic()
        if not force and self._settings.release_update_check_cache_seconds > 0:
            cached = _LATEST_CACHE.get(cache_key)
            if cached is not None:
                cached_at, payload = cached
                if now - cached_at <= self._settings.release_update_check_cache_seconds:
                    return ReleaseUpdateCheck(
                        current=payload.current,
                        latest=payload.latest,
                        update_available=payload.update_available,
                        release_url=payload.release_url,
                        assets=payload.assets,
                        cached=True,
                    )

        latest = self._fetch_latest_release()
        _LATEST_CACHE[cache_key] = (now, latest)
        return latest

    def update(
        self,
        tag: str,
        *,
        dry_run: bool,
        manifest_url: str | None = None,
        manifest_file: str | None = None,
        bundle_url: str | None = None,
        bundle_file: str | None = None,
        checksum_url: str | None = None,
        checksum_file: str | None = None,
        release_dir: str | None = None,
    ) -> ReleaseUpdateStart:
        return self._start(
            self.plan(
                "update",
                tag=tag,
                dry_run=dry_run,
                manifest_url=manifest_url,
                manifest_file=manifest_file,
                bundle_url=bundle_url,
                bundle_file=bundle_file,
                checksum_url=checksum_url,
                checksum_file=checksum_file,
                release_dir=release_dir,
            )
        )

    def rollback(
        self,
        *,
        dry_run: bool,
        release_dir: str | None = None,
    ) -> ReleaseUpdateStart:
        return self._start(
            self.plan("rollback", tag=None, dry_run=dry_run, release_dir=release_dir)
        )

    def restart(
        self,
        *,
        dry_run: bool,
        release_dir: str | None = None,
    ) -> ReleaseUpdateStart:
        return self._start(
            self.plan("restart", tag=None, dry_run=dry_run, release_dir=release_dir)
        )

    def plan(
        self,
        action: str,
        *,
        tag: str | None,
        dry_run: bool,
        manifest_url: str | None = None,
        manifest_file: str | None = None,
        bundle_url: str | None = None,
        bundle_file: str | None = None,
        checksum_url: str | None = None,
        checksum_file: str | None = None,
        release_dir: str | None = None,
    ) -> ReleaseUpdatePlan:
        if action not in {"update", "rollback", "restart"}:
            raise ValueError("Unsupported release update action")
        command = [
            self._settings.release_update_script,
            action,
            "--timeout-seconds",
            str(self._settings.release_update_timeout_seconds),
        ]
        manifest_url = _first_non_blank(
            manifest_url,
            self._settings.release_update_manifest_url,
        )
        manifest_file = _first_non_blank(
            manifest_file,
            self._settings.release_update_manifest_file,
        )
        bundle_url = _first_non_blank(
            bundle_url,
            self._settings.release_update_bundle_url,
        )
        bundle_file = _first_non_blank(
            bundle_file,
            self._settings.release_update_bundle_file,
        )
        checksum_url = _first_non_blank(
            checksum_url,
            self._settings.release_update_checksum_url,
        )
        checksum_file = _first_non_blank(
            checksum_file,
            self._settings.release_update_checksum_file,
        )
        release_dir = _first_non_blank(release_dir, self._settings.release_dir)
        normalized_tag: str | None = None
        if action == "update":
            if tag is None:
                raise ValueError("Release tag is required for update")
            normalized_tag = _normalize_release_tag(tag)
            command.extend(["--tag", normalized_tag])
            command.extend(_optional_command_args("--manifest-url", manifest_url))
            command.extend(_optional_command_args("--manifest-file", manifest_file))
            command.extend(_optional_command_args("--bundle-url", bundle_url))
            command.extend(_optional_command_args("--bundle-file", bundle_file))
            command.extend(_optional_command_args("--checksum-url", checksum_url))
            command.extend(_optional_command_args("--checksum-file", checksum_file))
        command.extend(_optional_command_args("--release-dir", release_dir))
        if dry_run:
            command.append("--dry-run")
        return ReleaseUpdatePlan(
            action=action,
            tag=normalized_tag,
            manifest_url=manifest_url,
            manifest_file=manifest_file,
            bundle_url=bundle_url,
            bundle_file=bundle_file,
            checksum_url=checksum_url,
            checksum_file=checksum_file,
            release_dir=release_dir,
            command=command,
            dry_run=dry_run,
        )

    def _start(self, plan: ReleaseUpdatePlan) -> ReleaseUpdateStart:
        if plan.dry_run:
            return ReleaseUpdateStart(
                action=plan.action,
                tag=plan.tag,
                manifest_url=plan.manifest_url,
                manifest_file=plan.manifest_file,
                bundle_url=plan.bundle_url,
                bundle_file=plan.bundle_file,
                checksum_url=plan.checksum_url,
                checksum_file=plan.checksum_file,
                release_dir=plan.release_dir,
                command=plan.command,
                dry_run=True,
                started=False,
                pid=None,
            )
        if not self._settings.release_update_enabled:
            raise RuntimeError(
                "Release updates are disabled; set OPSMESH_RELEASE_UPDATE_ENABLED=true"
            )
        if not os.path.isfile(self._settings.release_update_script):
            raise FileNotFoundError(self._settings.release_update_script)
        process = subprocess.Popen(  # noqa: S603
            plan.command,
            close_fds=True,
        )
        return ReleaseUpdateStart(
            action=plan.action,
            tag=plan.tag,
            manifest_url=plan.manifest_url,
            manifest_file=plan.manifest_file,
            bundle_url=plan.bundle_url,
            bundle_file=plan.bundle_file,
            checksum_url=plan.checksum_url,
            checksum_file=plan.checksum_file,
            release_dir=plan.release_dir,
            command=plan.command,
            dry_run=False,
            started=True,
            pid=process.pid,
        )

    def _fetch_latest_release(self) -> ReleaseUpdateCheck:
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
        latest_tag = _normalize_release_tag(str(payload["tag_name"]))
        latest = ReleaseVersion(
            version=latest_tag.removeprefix("v"),
            tag=latest_tag,
            commit=_release_commit(payload),
        )
        current = self.current_version()
        assets = [_asset_from_payload(item) for item in payload.get("assets", [])]
        return ReleaseUpdateCheck(
            current=current,
            latest=latest,
            update_available=_version_tuple(latest.tag) > _version_tuple(current.tag),
            release_url=str(payload.get("html_url") or ""),
            assets=assets,
            cached=False,
        )

    def _latest_release_url(self) -> str:
        repository = self._settings.release_update_repository.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Release update repository must look like owner/repo")
        base_url = self._settings.release_update_github_api_url.rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https":
            raise ValueError("Release update GitHub API URL must use HTTPS")
        return f"{base_url}/repos/{repository}/releases/latest"


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


def _normalize_release_tag(tag: str) -> str:
    candidate = tag.strip()
    if not RELEASE_TAG_PATTERN.fullmatch(candidate):
        raise ValueError("Release tag must be a semantic version such as v1.2.3")
    return candidate if candidate.startswith("v") else f"v{candidate}"


def _version_tuple(tag: str) -> tuple[int, int, int]:
    normalized = _normalize_release_tag(tag).removeprefix("v")
    core = normalized.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


def _first_non_blank(*values: str | None) -> str | None:
    for value in values:
        if value is None:
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _optional_command_args(flag: str, value: str | None) -> list[str]:
    return [flag, value] if value else []


def clear_release_update_cache() -> None:
    _LATEST_CACHE.clear()


def release_update_check_from_json(data: str) -> ReleaseUpdateCheck:
    payload = json.loads(data)
    return ReleaseUpdateCheck(
        current=ReleaseVersion(**payload["current"]),
        latest=ReleaseVersion(**payload["latest"]) if payload.get("latest") else None,
        update_available=bool(payload["update_available"]),
        release_url=payload.get("release_url"),
        assets=[ReleaseAsset(**asset) for asset in payload.get("assets", [])],
        cached=bool(payload.get("cached", False)),
    )
