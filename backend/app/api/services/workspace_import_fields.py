from datetime import datetime
from uuid import UUID


def _dt(value: datetime) -> str:
    return value.isoformat()


def _dt_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_or_none(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _string_field(item: dict[str, object], key: str, default: str = "") -> str:
    value = item.get(key, default)
    return value if isinstance(value, str) else default


def _optional_string_field(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _dict_field(item: dict[str, object], key: str) -> dict[str, object]:
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _remap_agent_skills(
    skills: dict[str, object],
    skill_install_id_map: dict[str, str],
) -> dict[str, object]:
    if not skill_install_id_map:
        return skills
    remapped = dict(skills)
    for key in ("installed_skill_ids", "skill_install_ids"):
        values = remapped.get(key)
        if not isinstance(values, list):
            continue
        remapped[key] = [
            skill_install_id_map.get(value, value) if isinstance(value, str) else value
            for value in values
        ]
    return remapped


def _optional_dict_field(item: dict[str, object], key: str) -> dict[str, object] | None:
    value = item.get(key)
    return value if isinstance(value, dict) else None


def _string_list_field(item: dict[str, object], key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _int_field(item: dict[str, object], key: str, default: int) -> int:
    value = item.get(key, default)
    return value if isinstance(value, int) else default


def _int_from_optional_string(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_field(item: dict[str, object], key: str, default: bool) -> bool:
    value = item.get(key, default)
    return value if isinstance(value, bool) else default


def _uuid_or_none(value: str | None) -> UUID | None:
    if not value:
        return None
    return UUID(value)


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


