from __future__ import annotations

from backend.app.admin.releases.models import ReleaseUpdatePlan
from backend.app.admin.releases.versioning import normalize_release_tag
from backend.app.core.config import Settings


class ReleaseUpdatePlanner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

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
        manifest_url = _first_non_blank(manifest_url, self._settings.release_update_manifest_url)
        manifest_file = _first_non_blank(
            manifest_file,
            self._settings.release_update_manifest_file,
        )
        bundle_url = _first_non_blank(bundle_url, self._settings.release_update_bundle_url)
        bundle_file = _first_non_blank(bundle_file, self._settings.release_update_bundle_file)
        checksum_url = _first_non_blank(checksum_url, self._settings.release_update_checksum_url)
        checksum_file = _first_non_blank(
            checksum_file,
            self._settings.release_update_checksum_file,
        )
        release_dir = _first_non_blank(release_dir, self._settings.release_dir)
        command = self._base_command(action)
        normalized_tag = _append_update_args(
            command,
            action=action,
            tag=tag,
            manifest_url=manifest_url,
            manifest_file=manifest_file,
            bundle_url=bundle_url,
            bundle_file=bundle_file,
            checksum_url=checksum_url,
            checksum_file=checksum_file,
        )
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

    def _base_command(self, action: str) -> list[str]:
        return [
            self._settings.release_update_script,
            action,
            "--timeout-seconds",
            str(self._settings.release_update_timeout_seconds),
        ]


def _append_update_args(
    command: list[str],
    *,
    action: str,
    tag: str | None,
    manifest_url: str | None,
    manifest_file: str | None,
    bundle_url: str | None,
    bundle_file: str | None,
    checksum_url: str | None,
    checksum_file: str | None,
) -> str | None:
    if action != "update":
        return None
    if tag is None:
        raise ValueError("Release tag is required for update")
    normalized_tag = normalize_release_tag(tag)
    command.extend(["--tag", normalized_tag])
    command.extend(_optional_command_args("--manifest-url", manifest_url))
    command.extend(_optional_command_args("--manifest-file", manifest_file))
    command.extend(_optional_command_args("--bundle-url", bundle_url))
    command.extend(_optional_command_args("--bundle-file", bundle_file))
    command.extend(_optional_command_args("--checksum-url", checksum_url))
    command.extend(_optional_command_args("--checksum-file", checksum_file))
    return normalized_tag


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
