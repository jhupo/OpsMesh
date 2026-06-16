from __future__ import annotations

import os
import subprocess

from backend.app.admin.releases.models import ReleaseUpdatePlan, ReleaseUpdateStart
from backend.app.core.config import Settings


class ReleaseUpdateExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def start(self, plan: ReleaseUpdatePlan) -> ReleaseUpdateStart:
        if plan.dry_run:
            return _start_response(plan, started=False, pid=None)
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
        return _start_response(plan, started=True, pid=process.pid)


def _start_response(
    plan: ReleaseUpdatePlan,
    *,
    started: bool,
    pid: int | None,
) -> ReleaseUpdateStart:
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
        dry_run=plan.dry_run,
        started=started,
        pid=pid,
    )
