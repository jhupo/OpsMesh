from collections.abc import Iterable
from datetime import UTC, datetime

from backend.app.core.typing import string_list


def _lifecycle_settings(settings: dict[str, object]) -> dict[str, object]:
    data_lifecycle = settings.get("data_lifecycle") if isinstance(settings, dict) else None
    return data_lifecycle if isinstance(data_lifecycle, dict) else {}


def _backup_settings(settings: dict[str, object]) -> dict[str, object]:
    lifecycle = _lifecycle_settings(settings)
    raw_policy = lifecycle.get("backup")
    return raw_policy if isinstance(raw_policy, dict) else {}


def _retention_settings(settings: dict[str, object]) -> dict[str, object]:
    lifecycle = _lifecycle_settings(settings)
    raw_policy = lifecycle.get("retention")
    return raw_policy if isinstance(raw_policy, dict) else {}


def _restore_drill_settings(settings: dict[str, object]) -> dict[str, object]:
    lifecycle = _lifecycle_settings(settings)
    raw_policy = lifecycle.get("restore_drill")
    return raw_policy if isinstance(raw_policy, dict) else {}


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _bool_setting(settings: dict[str, object], key: str, default: bool) -> bool:
    value = settings.get(key)
    return value if isinstance(value, bool) else default


def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_count_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(collection): int(count)
        for collection, count in value.items()
        if isinstance(count, int) and count >= 0
    }


def _safe_int(value: object) -> int:
    return int(value) if isinstance(value, int) and value >= 0 else 0


def _safe_conflict_summaries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    summaries: list[dict[str, object]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        summaries.append(
            {
                "collection": str(item.get("collection") or ""),
                "field": item.get("field") if isinstance(item.get("field"), str) else None,
                "strategy": str(item.get("strategy") or ""),
                "severity": str(item.get("severity") or ""),
            }
        )
    return summaries


def _string_list(value: object) -> list[str]:
    return string_list(value)


def _unique_strings(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized
