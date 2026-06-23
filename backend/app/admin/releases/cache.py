from __future__ import annotations

import time

from backend.app.admin.releases.models import ReleaseUpdateCheck

_LATEST_RELEASES: dict[str, tuple[float, ReleaseUpdateCheck]] = {}


def cached_release_check(
    key: str,
    *,
    max_age_seconds: int,
    now: float | None = None,
) -> ReleaseUpdateCheck | None:
    cached = _LATEST_RELEASES.get(key)
    if cached is None:
        return None
    cached_at, payload = cached
    if (now or time.monotonic()) - cached_at > max_age_seconds:
        return None
    return ReleaseUpdateCheck(
        current=payload.current,
        latest=payload.latest,
        update_available=payload.update_available,
        release_url=payload.release_url,
        assets=payload.assets,
        cached=True,
    )


def store_release_check(
    key: str,
    payload: ReleaseUpdateCheck,
    *,
    now: float | None = None,
) -> None:
    _LATEST_RELEASES[key] = (now or time.monotonic(), payload)


def clear_release_update_cache() -> None:
    _LATEST_RELEASES.clear()
