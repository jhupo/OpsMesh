from __future__ import annotations

from uuid import UUID


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def severity_rank(severity: str) -> int:
    return {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }.get(severity, 4)
