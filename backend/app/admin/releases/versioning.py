from __future__ import annotations

import re

RELEASE_TAG_PATTERN = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def normalize_release_tag(tag: str) -> str:
    candidate = tag.strip()
    if not RELEASE_TAG_PATTERN.fullmatch(candidate):
        raise ValueError("Release tag must be a semantic version such as v1.2.3")
    return candidate if candidate.startswith("v") else f"v{candidate}"


def version_tuple(tag: str) -> tuple[int, int, int]:
    normalized = normalize_release_tag(tag).removeprefix("v")
    core = normalized.split("-", 1)[0].split("+", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)
