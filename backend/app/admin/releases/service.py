from __future__ import annotations

import os
import time
from importlib.metadata import PackageNotFoundError, version

from backend.app.admin.releases.cache import cached_release_check, store_release_check
from backend.app.admin.releases.executor import ReleaseUpdateExecutor
from backend.app.admin.releases.github import GitHubReleaseClient
from backend.app.admin.releases.models import (
    ReleaseUpdateCheck,
    ReleaseUpdatePlan,
    ReleaseUpdateStart,
    ReleaseVersion,
)
from backend.app.admin.releases.planner import ReleaseUpdatePlanner
from backend.app.core.config import Settings


class ReleaseUpdateService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._planner = ReleaseUpdatePlanner(settings)
        self._executor = ReleaseUpdateExecutor(settings)
        self._github = GitHubReleaseClient(settings)

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
        return self._executor.start(
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
        return self._executor.start(
            self.plan("rollback", tag=None, dry_run=dry_run, release_dir=release_dir)
        )

    def restart(
        self,
        *,
        dry_run: bool,
        release_dir: str | None = None,
    ) -> ReleaseUpdateStart:
        return self._executor.start(
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
        return self._planner.plan(
            action,
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

    def _fetch_latest_release(self) -> ReleaseUpdateCheck:
        return self._github.fetch_latest(self.current_version())
