from __future__ import annotations

from dataclasses import dataclass


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
