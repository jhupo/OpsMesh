from __future__ import annotations

from packaging.version import InvalidVersion, Version


def release_version(tag: str) -> Version:
    candidate = tag.strip().removeprefix("v")
    try:
        version = Version(candidate)
    except InvalidVersion as exc:
        raise ValueError("Release tag must be a version such as v1.2.3") from exc
    if (
        version.epoch != 0
        or len(version.release) != 3
        or version.post is not None
        or version.dev is not None
    ):
        raise ValueError("Release tag must be a version such as v1.2.3")
    return version


def normalize_release_tag(tag: str) -> str:
    return f"v{release_version(tag)}"
