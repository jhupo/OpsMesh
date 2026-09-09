from __future__ import annotations

import os
import time
from importlib.metadata import version

from backend.app.admin.releases.cache import cached_release_check, store_release_check
from backend.app.admin.releases.github import GitHubReleaseClient
from backend.app.admin.releases.models import (
    ReleaseUpdateCheck,
    ReleaseVersion,
)
from backend.app.core.config import Settings


class ReleaseUpdateService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._github = GitHubReleaseClient(settings)

    def current_version(self) -> ReleaseVersion:
        package_version = version("opsmesh")
        return ReleaseVersion(
            version=package_version,
            tag=f"v{package_version}",
            commit=os.environ.get("OPSMESH_BUILD_COMMIT"),
        )

    def check_updates(self, *, force: bool = False) -> ReleaseUpdateCheck:
        cache_key = self._settings.release_update_repository
        now = time.monotonic()
        if not force and self._settings.release_update_check_cache_seconds > 0:
            cached = cached_release_check(
                cache_key,
                max_age_seconds=self._settings.release_update_check_cache_seconds,
                now=now,
            )
            if cached is not None:
                return cached
        latest = self._fetch_latest_release()
        store_release_check(cache_key, latest, now=now)
        return latest

    def _fetch_latest_release(self) -> ReleaseUpdateCheck:
        return self._github.fetch_latest(self.current_version())
