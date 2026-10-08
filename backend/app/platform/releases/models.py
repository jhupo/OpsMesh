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
