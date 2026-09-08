from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from backend.app.security.redaction import (
    is_sensitive_payload_key,
    is_sensitive_payload_value,
)

MAX_PROJECT_CONFIGURATION_BYTES = 65_536
_PROJECT_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RESERVED_PROJECT_PATH_ROOTS = frozenset({".opsmesh"})


def normalize_project_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 160:
        raise ValueError("Project name must be between 1 and 160 characters")
    return normalized


def normalize_project_slug(value: str) -> str:
    if not _PROJECT_SLUG_PATTERN.fullmatch(value) or len(value) > 80:
        raise ValueError("Project slug is invalid")
    return value


def normalize_project_description(value: str) -> str:
    normalized = value.strip()
    if len(normalized) > 2_000:
        raise ValueError("Project description exceeds maximum length")
    return normalized


def normalize_change_summary(value: str) -> str:
    normalized = value.strip()
    if len(normalized) > 500:
        raise ValueError("Project configuration change summary exceeds maximum length")
    if is_sensitive_payload_value(normalized):
        raise ValueError("Project configuration change summary cannot contain secret values")
    return normalized


def validate_output_declaration(
    *, artifact_type: str, content_type: str | None, max_bytes: int
) -> tuple[str, str | None, int]:
    if (
        not artifact_type
        or len(artifact_type) > 80
        or re.fullmatch(r"[a-zA-Z0-9_.-]+", artifact_type) is None
    ):
        raise ValueError("Project output artifact type is invalid")
    if content_type is not None and (not content_type or len(content_type) > 120):
        raise ValueError("Project output content type is invalid")
    if isinstance(max_bytes, bool) or not 1 <= max_bytes <= 1_073_741_824:
        raise ValueError("Project output byte limit is invalid")
    return artifact_type, content_type, max_bytes


def normalize_project_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("Project path is invalid")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or path.as_posix() in {"", "."} or ".." in path.parts:
        raise ValueError("Project path must be a safe relative path")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError("Project path must be normalized")
    normalized = path.as_posix()
    if normalized != value.strip():
        raise ValueError("Project path must be normalized")
    return normalized


def validate_project_layout(
    *, input_path: str, work_path: str, output_path: str
) -> tuple[str, str, str]:
    paths = tuple(normalize_project_path(item) for item in (input_path, work_path, output_path))
    if any(PurePosixPath(path).parts[0] in _RESERVED_PROJECT_PATH_ROOTS for path in paths):
        raise ValueError("Project layout uses a reserved internal path")
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if _is_same_or_descendant(left, right) or _is_same_or_descendant(right, left):
                raise ValueError("Project input, work, and output paths must not overlap")
    return paths


def require_path_within(path: str, directory: str, *, kind: str) -> str:
    normalized_path = normalize_project_path(path)
    normalized_directory = normalize_project_path(directory)
    if normalized_path == normalized_directory or not _is_same_or_descendant(
        normalized_path, normalized_directory
    ):
        raise ValueError(f"Project {kind} path must be inside {normalized_directory}")
    return normalized_path


def validate_project_configuration(value: dict[str, object]) -> dict[str, object]:
    if _contains_sensitive_value(value):
        raise ValueError("Project configuration cannot contain credentials or secret values")
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Project configuration must be JSON serializable") from exc
    if len(encoded) > MAX_PROJECT_CONFIGURATION_BYTES:
        raise ValueError("Project configuration exceeds maximum size")
    return value


def _contains_sensitive_value(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_payload_key(str(key)) or _contains_sensitive_value(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_value(item) for item in value)
    return isinstance(value, str) and is_sensitive_payload_value(value)


def _is_same_or_descendant(path: str, directory: str) -> bool:
    path_parts = PurePosixPath(path).parts
    directory_parts = PurePosixPath(directory).parts
    return path_parts[: len(directory_parts)] == directory_parts
