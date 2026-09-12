from __future__ import annotations

from uuid import UUID


def uuid_argument(arguments: dict[str, object], key: str) -> UUID:
    value = arguments.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    raise ValueError(f"Missing UUID argument: {key}")


def optional_uuid_argument(arguments: dict[str, object], key: str) -> UUID | None:
    value = arguments.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError(f"Invalid UUID argument: {key}")


def str_argument(arguments: dict[str, object], key: str, *, default: str) -> str:
    value = arguments.get(key, default)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    raise ValueError(f"Invalid string argument: {key}")


def optional_str_argument(arguments: dict[str, object], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError(f"Invalid string argument: {key}")


def dict_argument(arguments: dict[str, object], key: str) -> dict[str, object]:
    value = arguments.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(item_key): item_value for item_key, item_value in value.items()}
    raise ValueError(f"Invalid object argument: {key}")


def str_list_argument(arguments: dict[str, object], key: str) -> list[str]:
    value = arguments.get(key)
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return [item for item in value if isinstance(item, str)]
    raise ValueError(f"Invalid string list argument: {key}")


def optional_str_set_argument(arguments: dict[str, object], key: str) -> set[str] | None:
    items = str_list_argument(arguments, key)
    return set(items) if items else None


def bytes_argument(arguments: dict[str, object], key: str) -> bytes:
    value = arguments.get(key)
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ValueError(f"Invalid bytes argument: {key}")


def int_argument(arguments: dict[str, object], key: str, *, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"Invalid integer argument: {key}")
    if isinstance(value, int):
        return value
    raise ValueError(f"Invalid integer argument: {key}")


def optional_int_argument(arguments: dict[str, object], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Invalid integer argument: {key}")
    return value


def bool_argument(arguments: dict[str, object], key: str, *, default: bool) -> bool:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"Invalid boolean argument: {key}")
